import { describe, expect, it } from 'vitest'
import { readNdjsonStream } from '../../src/services/ndjson.ts'

function streamFrom(chunks) {
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(new TextEncoder().encode(chunk))
      controller.close()
    },
  })
}

describe('NDJSON response parser', () => {
  it('handles UTF-8 characters split across byte chunks, CRLF, multiple events and an unterminated tail', async () => {
    const source = [
      JSON.stringify({ type: 'meta', run_id: 8 }) + '\r\n',
      JSON.stringify({ type: 'delta', text: '健康' }) + '\n',
      JSON.stringify({ type: 'done', result: { reply: '健康' } }),
    ].join('')
    const bytes = new TextEncoder().encode(source)
    const chinese = new TextEncoder().encode('康')[0]
    const splitAt = bytes.indexOf(chinese) + 1
    const events = []

    const last = await readNdjsonStream(new ReadableStream({
      start(controller) {
        controller.enqueue(bytes.slice(0, splitAt))
        controller.enqueue(bytes.slice(splitAt))
        controller.close()
      },
    }), event => events.push(event))

    expect(events.map(event => event.type)).toEqual(['meta', 'delta', 'done'])
    expect(events[1].text).toBe('健康')
    expect(last.result).toEqual({ reply: '健康' })
  })

  it('passes unknown events through and ignores blank lines', async () => {
    const events = []
    await readNdjsonStream(streamFrom(['\n', '{"type":"future","value":1}\n', '\r\n']), event => events.push(event))
    expect(events).toEqual([{ type: 'future', value: 1 }])
  })

  it('rejects malformed JSON and non-event JSON values', async () => {
    await expect(readNdjsonStream(streamFrom(['{broken}\n']), () => undefined)).rejects.toThrow('无法识别的流式数据')
    await expect(readNdjsonStream(streamFrom(['[]\n']), () => undefined)).rejects.toThrow('无法识别的流式事件')
  })

  it('returns null when the stream ends without a done event', async () => {
    const result = await readNdjsonStream(streamFrom(['{"type":"meta"}\n']), () => undefined)
    expect(result).toBeNull()
  })
})
