export interface NdjsonEvent {
  type: string
  [key: string]: unknown
}

export async function readNdjsonStream(
  stream: ReadableStream<Uint8Array>,
  onEvent: (event: NdjsonEvent) => void,
): Promise<NdjsonEvent | null> {
  const reader = stream.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let finalEvent: NdjsonEvent | null = null

  const consumeLine = (line: string) => {
    const trimmed = line.trim()
    if (!trimmed) return
    let parsed: unknown
    try {
      parsed = JSON.parse(trimmed)
    } catch {
      throw new Error('服务端返回了无法识别的流式数据')
    }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)
      || typeof (parsed as Record<string, unknown>).type !== 'string') {
      throw new Error('服务端返回了无法识别的流式事件')
    }
    const event = parsed as NdjsonEvent
    onEvent(event)
    if (event.type === 'done') finalEvent = event
  }

  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() ?? ''
      for (const line of lines) consumeLine(line)
    }
    buffer += decoder.decode()
    if (buffer.trim()) consumeLine(buffer)
  } catch (error) {
    await reader.cancel(error).catch(() => undefined)
    throw error
  } finally {
    reader.releaseLock()
  }
  return finalEvent
}
