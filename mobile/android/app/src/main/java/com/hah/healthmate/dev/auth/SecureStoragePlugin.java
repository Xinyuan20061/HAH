package com.hah.healthmate.dev.auth;

import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.util.regex.Pattern;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

@CapacitorPlugin(name = "SecureStorage")
public class SecureStoragePlugin extends Plugin {
    private static final String PREFS = "hah_secure_v1";
    private static final String KEY_ALIAS = "hah_secure_storage_v1";
    private static final Pattern VALID_KEY = Pattern.compile("[A-Za-z0-9._-]{1,100}");
    private static final int GCM_TAG_BITS = 128;
    private static final int MAX_VALUE_BYTES = 256 * 1024;

    @PluginMethod
    public void set(PluginCall call) {
        String key = call.getString("key");
        String value = call.getString("value");
        if (!validKey(call, key) || value == null) {
            if (value == null) call.reject("缺少安全存储内容", "INVALID_ARGUMENT");
            return;
        }
        byte[] plaintext = value.getBytes(StandardCharsets.UTF_8);
        if (plaintext.length > MAX_VALUE_BYTES) {
            call.reject("安全存储内容过大", "VALUE_TOO_LARGE");
            return;
        }
        try {
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE, getOrCreateKey());
            byte[] iv = cipher.getIV();
            byte[] encrypted = cipher.doFinal(plaintext);
            ByteBuffer payload = ByteBuffer.allocate(iv.length + encrypted.length);
            payload.put(iv).put(encrypted);
            preferences().edit().putString(key, Base64.encodeToString(payload.array(), Base64.NO_WRAP)).apply();
            call.resolve();
        } catch (Exception error) {
            call.reject("无法安全保存本机凭据", "SECURE_STORAGE_FAILED");
        }
    }

    @PluginMethod
    public void get(PluginCall call) {
        String key = call.getString("key");
        if (!validKey(call, key)) return;
        String encoded = preferences().getString(key, null);
        JSObject result = new JSObject();
        if (encoded == null) {
            call.resolve(result);
            return;
        }
        try {
            byte[] payload = Base64.decode(encoded, Base64.NO_WRAP);
            int ivLength = 12;
            if (payload.length <= ivLength) throw new IllegalArgumentException("Invalid encrypted value");
            byte[] iv = new byte[ivLength];
            byte[] encrypted = new byte[payload.length - ivLength];
            System.arraycopy(payload, 0, iv, 0, ivLength);
            System.arraycopy(payload, ivLength, encrypted, 0, encrypted.length);
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, getOrCreateKey(), new GCMParameterSpec(GCM_TAG_BITS, iv));
            result.put("value", new String(cipher.doFinal(encrypted), StandardCharsets.UTF_8));
            call.resolve(result);
        } catch (Exception error) {
            preferences().edit().remove(key).apply();
            call.reject("本机凭据无法解密，已清除损坏记录", "SECURE_STORAGE_CORRUPT");
        }
    }

    @PluginMethod
    public void remove(PluginCall call) {
        String key = call.getString("key");
        if (!validKey(call, key)) return;
        preferences().edit().remove(key).apply();
        call.resolve();
    }

    private SharedPreferences preferences() {
        return getContext().getSharedPreferences(PREFS, 0);
    }

    private boolean validKey(PluginCall call, String key) {
        if (key == null || !VALID_KEY.matcher(key).matches()) {
            call.reject("安全存储键无效", "INVALID_ARGUMENT");
            return false;
        }
        return true;
    }

    private SecretKey getOrCreateKey() throws Exception {
        KeyStore keyStore = KeyStore.getInstance("AndroidKeyStore");
        keyStore.load(null);
        java.security.Key existing = keyStore.getKey(KEY_ALIAS, null);
        if (existing instanceof SecretKey) return (SecretKey) existing;

        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
        generator.init(new KeyGenParameterSpec.Builder(
                KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setRandomizedEncryptionRequired(true)
                .build());
        return generator.generateKey();
    }
}
