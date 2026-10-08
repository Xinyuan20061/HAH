package com.hah.healthmate.dev;

import com.getcapacitor.BridgeActivity;
import com.hah.healthmate.dev.auth.SecureStoragePlugin;
import com.hah.healthmate.dev.auth.WechatAuthPlugin;
import com.hah.healthmate.dev.auth.VoiceCapturePlugin;
import com.hah.healthmate.dev.auth.NativeMediaPlugin;

import android.os.Bundle;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(SecureStoragePlugin.class);
        registerPlugin(WechatAuthPlugin.class);
        registerPlugin(VoiceCapturePlugin.class);
        registerPlugin(NativeMediaPlugin.class);
        super.onCreate(savedInstanceState);
    }
}
