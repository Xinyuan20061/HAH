package com.hah.healthmate.dev.auth;

import android.os.Handler;
import android.os.Looper;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.hah.healthmate.dev.BuildConfig;
import com.tencent.mm.opensdk.modelmsg.SendAuth;
import com.tencent.mm.opensdk.openapi.IWXAPI;
import com.tencent.mm.opensdk.openapi.WXAPIFactory;

import java.lang.ref.WeakReference;
import java.util.UUID;

@CapacitorPlugin(name = "WechatAuth")
public class WechatAuthPlugin extends Plugin {
    private static final Handler MAIN = new Handler(Looper.getMainLooper());
    private static WeakReference<WechatAuthPlugin> activePlugin = new WeakReference<>(null);
    private PluginCall pendingCall;
    private String pendingState;
    private Runnable timeout;

    @Override
    public void load() {
        activePlugin = new WeakReference<>(this);
    }

    @PluginMethod
    public void login(PluginCall call) {
        String appId = BuildConfig.WECHAT_MOBILE_APP_ID;
        if (appId == null || appId.trim().isEmpty()) {
            call.reject("尚未配置微信开放平台 AppID", "WECHAT_NOT_CONFIGURED");
            return;
        }
        if (pendingCall != null) {
            call.reject("微信授权正在进行，请稍候", "WECHAT_AUTH_IN_PROGRESS");
            return;
        }

        IWXAPI api = WXAPIFactory.createWXAPI(getContext(), appId, true);
        api.registerApp(appId);
        if (!api.isWXAppInstalled()) {
            call.reject("请先安装微信，再使用微信登录", "WECHAT_NOT_INSTALLED");
            return;
        }

        pendingCall = call;
        pendingCall.setKeepAlive(true);
        pendingState = UUID.randomUUID().toString().replace("-", "");
        SendAuth.Req request = new SendAuth.Req();
        request.scope = "snsapi_userinfo";
        request.state = pendingState;
        if (!api.sendReq(request)) {
            rejectPending("无法打开微信授权", "WECHAT_AUTH_UNAVAILABLE");
            return;
        }
        timeout = () -> rejectPending("微信授权超时，请重试", "WECHAT_AUTH_TIMEOUT");
        MAIN.postDelayed(timeout, 180_000L);
    }

    public static void deliverAuthResult(int errCode, String code, String state) {
        MAIN.post(() -> {
            WechatAuthPlugin plugin = activePlugin.get();
            if (plugin == null || plugin.pendingCall == null) return;
            if (state == null || !state.equals(plugin.pendingState)) {
                plugin.rejectPending("微信授权状态校验失败，请重试", "WECHAT_AUTH_STATE_MISMATCH");
            } else if (errCode == 0 && code != null && !code.isEmpty()) {
                PluginCall call = plugin.pendingCall;
                plugin.clearPending();
                JSObject result = new JSObject();
                result.put("code", code);
                result.put("state", state);
                call.resolve(result);
            } else if (errCode == -2) {
                plugin.rejectPending("已取消微信授权", "WECHAT_AUTH_CANCELLED");
            } else {
                plugin.rejectPending("微信授权失败，请重试", "WECHAT_AUTH_FAILED");
            }
        });
    }

    private void rejectPending(String message, String code) {
        PluginCall call = pendingCall;
        clearPending();
        if (call != null) call.reject(message, code);
    }

    private void clearPending() {
        if (timeout != null) MAIN.removeCallbacks(timeout);
        timeout = null;
        pendingState = null;
        pendingCall = null;
    }
}
