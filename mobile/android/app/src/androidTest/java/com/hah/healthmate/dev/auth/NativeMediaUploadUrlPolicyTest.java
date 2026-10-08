package com.hah.healthmate.dev.auth;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import android.content.Context;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import com.getcapacitor.JSObject;
import java.net.URL;
import org.junit.Test;
import org.junit.runner.RunWith;

@RunWith(AndroidJUnit4.class)
public class NativeMediaUploadUrlPolicyTest {
    @Test
    public void debugBuildAllowsOnlyEmulatorHostForLocalHttp() throws Exception {
        assertTrue(NativeMediaUploadUrlPolicy.allows(
                new URL("http://10.0.2.2:9000/upload?signature=test"), true));
        assertFalse(NativeMediaUploadUrlPolicy.allows(
                new URL("http://10.0.2.2:9000/upload?signature=test"), false));
        assertFalse(NativeMediaUploadUrlPolicy.allows(
                new URL("http://localhost:9000/upload"), true));
        assertFalse(NativeMediaUploadUrlPolicy.allows(
                new URL("http://10.0.2.2.evil.invalid/upload"), true));
    }

    @Test
    public void httpsRemainsAvailableForBothBuildTypes() throws Exception {
        URL url = new URL("https://bucket.example.invalid/upload?signature=test");
        assertTrue(NativeMediaUploadUrlPolicy.allows(url, true));
        assertTrue(NativeMediaUploadUrlPolicy.allows(url, false));
    }

    @Test
    public void rejectsEmbeddedCredentialsAndFragments() throws Exception {
        assertFalse(NativeMediaUploadUrlPolicy.allows(
                new URL("https://user:password@bucket.example.invalid/upload"), false));
        assertFalse(NativeMediaUploadUrlPolicy.allows(
                new URL("https://bucket.example.invalid/upload#fragment"), false));
    }

    @Test
    public void debugApkUsesTheDevelopmentPackage() {
        Context context = InstrumentationRegistry.getInstrumentation().getTargetContext();
        assertEquals("com.hah.healthmate.dev", context.getPackageName());
    }

    @Test
    public void acceptsIntegerByteCountsFromCapacitorJson() throws Exception {
        JSObject request = new JSObject();
        request.put("size_bytes", 1_704_475);

        assertEquals(1_704_475L, NativeMediaPlugin.requestedSizeBytes(
                request.opt("size_bytes")));
        assertEquals(-1L, NativeMediaPlugin.requestedSizeBytes(1_704_475.5d));
    }
}
