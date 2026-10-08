package com.hah.healthmate.dev.auth;

import java.net.URL;

final class NativeMediaUploadUrlPolicy {
    private NativeMediaUploadUrlPolicy() {}

    static boolean allows(URL url, boolean debugBuild) {
        if (url == null || url.getUserInfo() != null || url.getRef() != null) return false;
        if ("https".equalsIgnoreCase(url.getProtocol())) return true;
        return debugBuild
                && "http".equalsIgnoreCase(url.getProtocol())
                && "10.0.2.2".equalsIgnoreCase(url.getHost());
    }
}
