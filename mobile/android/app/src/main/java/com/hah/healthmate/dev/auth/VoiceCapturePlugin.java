package com.hah.healthmate.dev.auth;

import android.Manifest;
import android.content.Context;
import android.media.AudioAttributes;
import android.media.AudioFocusRequest;
import android.media.AudioManager;
import android.media.MediaRecorder;
import android.os.Build;
import android.util.Base64;

import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;

@CapacitorPlugin(
        name = "VoiceCapture",
        permissions = {@Permission(alias = "microphone", strings = {Manifest.permission.RECORD_AUDIO})}
)
public class VoiceCapturePlugin extends Plugin {
    private MediaRecorder recorder;
    private File recordingFile;
    private long recordingStartedAt;
    private AudioManager audioManager;
    private AudioFocusRequest audioFocusRequest;
    private final AudioManager.OnAudioFocusChangeListener audioFocusChangeListener = focusChange -> {
        if (focusChange < 0 && recorder != null) {
            discardRecording();
            JSObject event = new JSObject();
            event.put("reason", "audio_focus_lost");
            notifyListeners("recordingInterrupted", event);
        }
    };

    @Override
    public void load() {
        File cache = getContext().getCacheDir();
        File[] stale = cache.listFiles((directory, name) ->
                name.startsWith("healthmate-voice-") && name.endsWith(".m4a"));
        if (stale == null) return;
        for (File file : stale) file.delete();
    }

    @PluginMethod
    public void startRecording(PluginCall call) {
        if (getPermissionState("microphone") == PermissionState.GRANTED) {
            beginRecording(call);
            return;
        }
        requestPermissionForAlias("microphone", call, "microphonePermissionResult");
    }

    @PermissionCallback
    private void microphonePermissionResult(PluginCall call) {
        if (getPermissionState("microphone") != PermissionState.GRANTED) {
            call.reject("需要麦克风权限才能进行语音输入", "MICROPHONE_PERMISSION_DENIED");
            return;
        }
        beginRecording(call);
    }

    @PluginMethod
    public void stopRecording(PluginCall call) {
        if (recorder == null || recordingFile == null) {
            call.reject("当前没有正在进行的录音", "RECORDING_NOT_STARTED");
            return;
        }

        MediaRecorder activeRecorder = recorder;
        File completedFile = recordingFile;
        long durationMs = Math.max(0, System.currentTimeMillis() - recordingStartedAt);
        recorder = null;
        recordingFile = null;
        try {
            activeRecorder.stop();
            activeRecorder.reset();
            activeRecorder.release();
            if (!completedFile.isFile() || completedFile.length() == 0) {
                completedFile.delete();
                call.reject("没有录到声音，请重试", "EMPTY_RECORDING");
                return;
            }

            byte[] audio = readFile(completedFile);
            completedFile.delete();
            JSObject result = new JSObject();
            result.put("audio_base64", Base64.encodeToString(audio, Base64.NO_WRAP));
            result.put("duration_ms", durationMs);
            result.put("format", "m4a");
            result.put("mime_type", "audio/mp4");
            call.resolve(result);
        } catch (RuntimeException | IOException error) {
            release(activeRecorder);
            completedFile.delete();
            call.reject("录音文件读取失败，请重试", "RECORDING_FAILED");
        } finally {
            abandonAudioFocus();
        }
    }

    @PluginMethod
    public void cancelRecording(PluginCall call) {
        discardRecording();
        JSObject result = new JSObject();
        result.put("cancelled", true);
        call.resolve(result);
    }

    @Override
    protected void handleOnPause() {
        discardRecording();
    }

    @Override
    protected void handleOnDestroy() {
        discardRecording();
    }

    private void beginRecording(PluginCall call) {
        if (recorder != null) {
            call.reject("录音已经开始", "ALREADY_RECORDING");
            return;
        }
        if (!requestAudioFocus()) {
            abandonAudioFocus();
            call.reject("其他应用正在使用音频，请稍后再试", "AUDIO_FOCUS_UNAVAILABLE");
            return;
        }
        MediaRecorder next = new MediaRecorder();
        File output = null;
        try {
            output = File.createTempFile("healthmate-voice-", ".m4a", getContext().getCacheDir());
            next.setAudioSource(MediaRecorder.AudioSource.MIC);
            next.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4);
            next.setAudioEncoder(MediaRecorder.AudioEncoder.AAC);
            next.setAudioSamplingRate(16000);
            next.setAudioChannels(1);
            next.setAudioEncodingBitRate(48000);
            next.setOutputFile(output.getAbsolutePath());
            next.prepare();
            next.start();
            recorder = next;
            recordingFile = output;
            recordingStartedAt = System.currentTimeMillis();
            call.resolve();
        } catch (Exception error) {
            release(next);
            if (output != null) output.delete();
            abandonAudioFocus();
            call.reject("无法启动录音，请确认麦克风没有被其他应用占用", "RECORDING_FAILED");
        }
    }

    private boolean requestAudioFocus() {
        audioManager = (AudioManager) getContext().getSystemService(Context.AUDIO_SERVICE);
        if (audioManager == null) return false;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            AudioAttributes attributes = new AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build();
            audioFocusRequest = new AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT)
                    .setAudioAttributes(attributes)
                    .setWillPauseWhenDucked(true)
                    .setOnAudioFocusChangeListener(audioFocusChangeListener)
                    .build();
            return audioManager.requestAudioFocus(audioFocusRequest) == AudioManager.AUDIOFOCUS_REQUEST_GRANTED;
        }
        return audioManager.requestAudioFocus(
                audioFocusChangeListener,
                AudioManager.STREAM_VOICE_CALL,
                AudioManager.AUDIOFOCUS_GAIN_TRANSIENT
        ) == AudioManager.AUDIOFOCUS_REQUEST_GRANTED;
    }

    private void abandonAudioFocus() {
        AudioManager activeManager = audioManager;
        AudioFocusRequest activeRequest = audioFocusRequest;
        audioManager = null;
        audioFocusRequest = null;
        if (activeManager == null) return;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && activeRequest != null) {
            activeManager.abandonAudioFocusRequest(activeRequest);
        } else {
            activeManager.abandonAudioFocus(audioFocusChangeListener);
        }
    }

    private byte[] readFile(File file) throws IOException {
        try (FileInputStream input = new FileInputStream(file)) {
            byte[] bytes = new byte[(int) file.length()];
            int offset = 0;
            while (offset < bytes.length) {
                int count = input.read(bytes, offset, bytes.length - offset);
                if (count < 0) break;
                offset += count;
            }
            if (offset != bytes.length) throw new IOException("Incomplete recording file");
            return bytes;
        }
    }

    private void discardRecording() {
        MediaRecorder active = recorder;
        File file = recordingFile;
        recorder = null;
        recordingFile = null;
        if (active != null) release(active);
        if (file != null) file.delete();
        abandonAudioFocus();
    }

    private void release(MediaRecorder active) {
        try {
            active.stop();
        } catch (RuntimeException ignored) {
            // A short or interrupted recording can fail stop(); always release it.
        }
        try {
            active.reset();
        } catch (RuntimeException ignored) {
        }
        try {
            active.release();
        } catch (RuntimeException ignored) {
        }
    }
}
