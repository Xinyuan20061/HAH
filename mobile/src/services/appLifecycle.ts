import { App } from '@capacitor/app'
import { Capacitor, type PluginListenerHandle } from '@capacitor/core'

/** Pause background work that should resume when the native app is visible. */
export async function waitForAppForeground(signal?: AbortSignal): Promise<void> {
  if (!Capacitor.isNativePlatform() || signal?.aborted) return

  try {
    if ((await App.getState()).isActive) return
  } catch {
    // Keep the task usable if a platform does not expose app state reliably.
    return
  }

  await new Promise<void>(resolve => {
    let listener: PluginListenerHandle | null = null
    let settled = false

    const finish = () => {
      if (settled) return
      settled = true
      signal?.removeEventListener('abort', finish)
      if (listener) void listener.remove()
      resolve()
    }

    const registration = App.addListener('appStateChange', state => {
      if (state.isActive) finish()
    })
    registration.then(async handle => {
      listener = handle
      if (settled) {
        void handle.remove()
        return
      }
      try {
        // Close the gap between the first state read and listener registration.
        if ((await App.getState()).isActive) finish()
      } catch {
        finish()
      }
    }).catch(finish)

    signal?.addEventListener('abort', finish, { once: true })
    if (signal?.aborted) finish()
  })
}
