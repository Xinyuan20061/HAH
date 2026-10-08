package com.hah.healthmate.dev.auth;

import android.app.Activity;
import android.content.ClipData;
import android.content.Intent;
import android.database.Cursor;
import android.graphics.Bitmap;
import android.media.MediaMetadataRetriever;
import android.net.Uri;
import android.os.Build;
import android.provider.MediaStore;
import android.provider.OpenableColumns;
import android.util.Base64;
import android.webkit.MimeTypeMap;

import androidx.activity.result.ActivityResult;
import androidx.core.content.FileProvider;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.ActivityCallback;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.hah.healthmate.dev.BuildConfig;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.Iterator;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

@CapacitorPlugin(name = "NativeMedia")
public class NativeMediaPlugin extends Plugin {
    private static final String CAPTURE_DIRECTORY = "hah-video-capture";
    private static final int STREAM_BUFFER_BYTES = 64 * 1024;
    private static final int PROGRESS_STEP_BYTES = 512 * 1024;

    private final Set<String> selectedUris = ConcurrentHashMap.newKeySet();
    private final Set<String> cancelledTransfers = ConcurrentHashMap.newKeySet();
    private final ConcurrentHashMap<String, HttpURLConnection> activeConnections =
            new ConcurrentHashMap<>();
    private final ConcurrentHashMap<String, File> capturedFiles = new ConcurrentHashMap<>();
    private File pendingCaptureFile;

    @Override
    public void load() {
        File directory = new File(getContext().getCacheDir(), CAPTURE_DIRECTORY);
        File[] stale = directory.listFiles();
        if (stale != null) {
            for (File file : stale) file.delete();
        }
        directory.mkdirs();
    }

    @PluginMethod
    public void pickVideo(PluginCall call) {
        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType("video/*");
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION
                | Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION);
        startActivityForResult(call, intent, "videoPickerResult");
    }

    @ActivityCallback
    private void videoPickerResult(PluginCall call, ActivityResult result) {
        if (result.getResultCode() != Activity.RESULT_OK || result.getData() == null
                || result.getData().getData() == null) {
            call.reject("已取消视频选择", "MEDIA_PICKER_CANCELLED");
            return;
        }
        Uri uri = result.getData().getData();
        try {
            getContext().getContentResolver().takePersistableUriPermission(
                    uri, Intent.FLAG_GRANT_READ_URI_PERMISSION);
        } catch (SecurityException ignored) {
            // Some document providers grant a temporary read-only URI instead.
        }
        returnVideoMetadata(call, uri, null);
    }

    @PluginMethod
    public void captureVideo(PluginCall call) {
        Intent intent = new Intent(MediaStore.ACTION_VIDEO_CAPTURE);
        if (intent.resolveActivity(getContext().getPackageManager()) == null) {
            call.reject("当前设备没有可用的视频相机", "VIDEO_CAMERA_UNAVAILABLE");
            return;
        }
        try {
            File directory = new File(getContext().getCacheDir(), CAPTURE_DIRECTORY);
            directory.mkdirs();
            File output = File.createTempFile("capture-", ".mp4", directory);
            Uri outputUri = FileProvider.getUriForFile(
                    getContext(), getContext().getPackageName() + ".fileprovider", output);
            intent.putExtra(MediaStore.EXTRA_OUTPUT, outputUri);
            intent.putExtra(MediaStore.EXTRA_VIDEO_QUALITY, 1);
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION
                    | Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
            intent.setClipData(ClipData.newUri(
                    getContext().getContentResolver(), "HealthMate video", outputUri));
            pendingCaptureFile = output;
            startActivityForResult(call, intent, "videoCaptureResult");
        } catch (Exception error) {
            pendingCaptureFile = null;
            call.reject("无法启动视频录制，请重试", "VIDEO_CAPTURE_FAILED");
        }
    }

    @ActivityCallback
    private void videoCaptureResult(PluginCall call, ActivityResult result) {
        File output = pendingCaptureFile;
        pendingCaptureFile = null;
        if (result.getResultCode() != Activity.RESULT_OK) {
            if (output != null) output.delete();
            call.reject("已取消视频录制", "MEDIA_PICKER_CANCELLED");
            return;
        }
        if (output == null || !output.isFile() || output.length() == 0) {
            if (output != null) output.delete();
            Uri returnedUri = result.getData() == null ? null : result.getData().getData();
            if (returnedUri != null) {
                returnVideoMetadata(call, returnedUri, null);
                return;
            }
            call.reject("视频录制没有生成可读取的文件", "VIDEO_CAPTURE_EMPTY");
            return;
        }
        Uri uri = FileProvider.getUriForFile(
                getContext(), getContext().getPackageName() + ".fileprovider", output);
        capturedFiles.put(uri.toString(), output);
        returnVideoMetadata(call, uri, output);
    }

    private void returnVideoMetadata(PluginCall call, Uri uri, File capturedFile) {
        try {
            String name = capturedFile == null ? readDisplayName(uri) : capturedFile.getName();
            long size = capturedFile == null ? readSize(uri) : capturedFile.length();
            String contentType = getContext().getContentResolver().getType(uri);
            if (contentType == null || contentType.trim().isEmpty()) {
                contentType = mimeFromName(name);
            }
            if (contentType != null) contentType = contentType.toLowerCase(java.util.Locale.ROOT);
            String extension = extension(name);
            if ("video/quicktime".equalsIgnoreCase(contentType)) {
                extension = "mov";
            } else if ("video/mp4".equalsIgnoreCase(contentType)) {
                extension = "mp4";
            }
            if (size <= 0 || extension == null
                    || !("mp4".equals(extension) || "mov".equals(extension))) {
                if (capturedFile != null) capturedFile.delete();
                releasePersistedUri(uri);
                call.reject("当前仅支持 MP4 或 MOV 视频", "UNSUPPORTED_VIDEO_FORMAT");
                return;
            }
            contentType = "mov".equals(extension) ? "video/quicktime" : "video/mp4";
            selectedUris.add(uri.toString());
            JSObject response = new JSObject();
            response.put("uri", uri.toString());
            response.put("file_name", name == null || name.isEmpty()
                    ? "healthmate." + extension : name);
            response.put("content_type", contentType);
            response.put("size_bytes", size);
            String thumbnail = thumbnailDataUrl(uri);
            if (thumbnail != null) response.put("thumbnail_data_url", thumbnail);
            call.resolve(response);
        } catch (Exception error) {
            if (capturedFile != null) capturedFile.delete();
            call.reject("无法读取所选视频，请重新选择", "VIDEO_METADATA_UNAVAILABLE");
        }
    }

    private String readDisplayName(Uri uri) {
        try (Cursor cursor = getContext().getContentResolver().query(
                uri, new String[]{OpenableColumns.DISPLAY_NAME}, null, null, null)) {
            if (cursor != null && cursor.moveToFirst()) {
                int index = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME);
                if (index >= 0) return cursor.getString(index);
            }
        } catch (Exception ignored) {
        }
        String last = uri.getLastPathSegment();
        return last == null ? "healthmate-video.mp4" : last;
    }

    private long readSize(Uri uri) {
        try (Cursor cursor = getContext().getContentResolver().query(
                uri, new String[]{OpenableColumns.SIZE}, null, null, null)) {
            if (cursor != null && cursor.moveToFirst()) {
                int index = cursor.getColumnIndex(OpenableColumns.SIZE);
                if (index >= 0 && !cursor.isNull(index)) return cursor.getLong(index);
            }
        } catch (Exception ignored) {
        }
        try (android.content.res.AssetFileDescriptor descriptor =
                     getContext().getContentResolver().openAssetFileDescriptor(uri, "r")) {
            return descriptor == null ? -1 : descriptor.getLength();
        } catch (Exception ignored) {
            return -1;
        }
    }

    private String thumbnailDataUrl(Uri uri) {
        MediaMetadataRetriever retriever = new MediaMetadataRetriever();
        Bitmap frame = null;
        Bitmap scaled = null;
        try {
            retriever.setDataSource(getContext(), uri);
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
                frame = retriever.getScaledFrameAtTime(
                        0, MediaMetadataRetriever.OPTION_CLOSEST_SYNC, 480, 480);
            } else {
                frame = retriever.getFrameAtTime(
                        0, MediaMetadataRetriever.OPTION_CLOSEST_SYNC);
            }
            if (frame == null) return null;
            int longest = Math.max(frame.getWidth(), frame.getHeight());
            if (longest > 480) {
                float ratio = 480f / longest;
                scaled = Bitmap.createScaledBitmap(
                        frame, Math.max(1, Math.round(frame.getWidth() * ratio)),
                        Math.max(1, Math.round(frame.getHeight() * ratio)), true);
            } else {
                scaled = frame;
            }
            ByteArrayOutputStream output = new ByteArrayOutputStream();
            scaled.compress(Bitmap.CompressFormat.JPEG, 72, output);
            return "data:image/jpeg;base64," + Base64.encodeToString(output.toByteArray(), Base64.NO_WRAP);
        } catch (Exception ignored) {
            return null;
        } finally {
            try {
                retriever.release();
            } catch (IOException ignored) {
            }
            if (scaled != null && scaled != frame) scaled.recycle();
            if (frame != null) frame.recycle();
        }
    }

    private String mimeFromName(String name) {
        String extension = extension(name);
        if (extension == null) return null;
        String mime = MimeTypeMap.getSingleton().getMimeTypeFromExtension(extension);
        return mime;
    }

    private String extension(String name) {
        if (name == null) return null;
        int dot = name.lastIndexOf('.');
        if (dot < 0 || dot == name.length() - 1) return null;
        return name.substring(dot + 1).toLowerCase(java.util.Locale.ROOT);
    }

    @PluginMethod
    public void uploadFile(PluginCall call) {
        String uriText = call.getString("uri", "");
        String transferId = call.getString("transfer_id", "");
        String uploadUrl = call.getString("upload_url", "");
        String contentType = call.getString("content_type", "");
        long requestedSize = requestedSizeBytes(call.getData().opt("size_bytes"));
        if (uriText.isEmpty() || transferId.isEmpty() || uploadUrl.isEmpty()
                || contentType.isEmpty() || requestedSize <= 0) {
            call.reject("视频上传参数不完整", "INVALID_MEDIA_UPLOAD");
            return;
        }
        if (!selectedUris.contains(uriText)) {
            call.reject("视频授权已失效，请重新选择", "MEDIA_URI_NOT_AUTHORIZED");
            return;
        }
        URL parsed;
        try {
            parsed = new URL(uploadUrl);
            if (!NativeMediaUploadUrlPolicy.allows(parsed, BuildConfig.DEBUG)) {
                call.reject("媒体上传必须使用 HTTPS", "INSECURE_MEDIA_UPLOAD_URL");
                return;
            }
        } catch (Exception error) {
            call.reject("媒体上传地址无效", "INVALID_MEDIA_UPLOAD_URL");
            return;
        }

        JSObject headers = call.getObject("headers", new JSObject());
        long sizeBytes = requestedSize;
        call.setKeepAlive(true);
        new Thread(() -> streamUpload(
                call, uriText, transferId, parsed, contentType, sizeBytes, headers),
                "hah-native-video-upload").start();
    }

    static long requestedSizeBytes(Object value) {
        if (!(value instanceof Number)) return -1L;
        Number size = (Number) value;
        long bytes = size.longValue();
        double numericValue = size.doubleValue();
        if (bytes <= 0 || !Double.isFinite(numericValue) || numericValue != (double) bytes) {
            return -1L;
        }
        return bytes;
    }

    private void streamUpload(
            PluginCall call,
            String uriText,
            String transferId,
            URL uploadUrl,
            String contentType,
            long sizeBytes,
            JSObject headers) {
        HttpURLConnection connection = null;
        try {
            Uri uri = Uri.parse(uriText);
            long actualSize = readSize(uri);
            if (actualSize != sizeBytes) {
                throw new IOException("视频大小已变化，请重新选择");
            }
            connection = (HttpURLConnection) uploadUrl.openConnection();
            connection.setRequestMethod("PUT");
            connection.setConnectTimeout(30_000);
            connection.setReadTimeout(180_000);
            connection.setDoOutput(true);
            connection.setUseCaches(false);
            connection.setFixedLengthStreamingMode(sizeBytes);
            connection.setRequestProperty("Content-Type", contentType);
            activeConnections.put(transferId, connection);
            if (cancelledTransfers.contains(transferId)) {
                throw new IOException("上传已取消");
            }
            Iterator<String> keys = headers.keys();
            while (keys.hasNext()) {
                String key = keys.next();
                if ("Content-Type".equalsIgnoreCase(key)) continue;
                if (key.startsWith("x-cos-") || key.startsWith("x-amz-")) {
                    connection.setRequestProperty(key, headers.optString(key));
                }
            }

            try (InputStream input = getContext().getContentResolver().openInputStream(uri);
                 OutputStream output = connection.getOutputStream()) {
                if (input == null) throw new IOException("无法打开所选视频");
                byte[] buffer = new byte[STREAM_BUFFER_BYTES];
                long sent = 0;
                long lastProgress = 0;
                int count;
                while (sent < sizeBytes) {
                    if (cancelledTransfers.contains(transferId)) {
                        throw new IOException("上传已取消");
                    }
                    int maximum = (int) Math.min(buffer.length, sizeBytes - sent);
                    count = input.read(buffer, 0, maximum);
                    if (count < 0) throw new IOException("视频读取提前结束，请重试");
                    if (count == 0) continue;
                    output.write(buffer, 0, count);
                    sent += count;
                    if (sent - lastProgress >= PROGRESS_STEP_BYTES || sent == sizeBytes) {
                        lastProgress = sent;
                        JSObject progress = new JSObject();
                        progress.put("transfer_id", transferId);
                        progress.put("sent_bytes", sent);
                        progress.put("total_bytes", sizeBytes);
                        progress.put("percent", Math.min(100, Math.round(sent * 100f / sizeBytes)));
                        notifyListeners("uploadProgress", progress);
                    }
                }
                if (input.read() != -1) throw new IOException("视频内容已变化，请重新选择");
                output.flush();
            }

            int status = connection.getResponseCode();
            if (status < 200 || status >= 300) {
                throw new IOException("COS 上传失败（HTTP " + status + "）");
            }
            JSObject result = new JSObject();
            result.put("status", status);
            getBridge().executeOnMainThread(() -> {
                call.setKeepAlive(false);
                call.resolve(result);
            });
        } catch (Exception error) {
            String message = error.getMessage() == null ? "视频上传失败，请重试" : error.getMessage();
            getBridge().executeOnMainThread(() -> {
                call.setKeepAlive(false);
                call.reject(message, "NATIVE_MEDIA_UPLOAD_FAILED");
            });
        } finally {
            activeConnections.remove(transferId);
            cancelledTransfers.remove(transferId);
            if (connection != null) connection.disconnect();
        }
    }

    @PluginMethod
    public void cancelUpload(PluginCall call) {
        String transferId = call.getString("transfer_id", "");
        if (!transferId.isEmpty()) {
            cancelledTransfers.add(transferId);
            HttpURLConnection connection = activeConnections.get(transferId);
            if (connection != null) connection.disconnect();
        }
        call.resolve();
    }

    @PluginMethod
    public void releaseMedia(PluginCall call) {
        String uriText = call.getString("uri", "");
        if (!uriText.isEmpty()) {
            selectedUris.remove(uriText);
            File capture = capturedFiles.remove(uriText);
            if (capture != null) capture.delete();
            releasePersistedUri(Uri.parse(uriText));
        }
        call.resolve();
    }

    private void releasePersistedUri(Uri uri) {
        try {
            getContext().getContentResolver().releasePersistableUriPermission(
                    uri, Intent.FLAG_GRANT_READ_URI_PERMISSION);
        } catch (SecurityException ignored) {
        }
    }
}
