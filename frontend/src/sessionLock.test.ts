import { describe, it, expect, vi, afterEach } from 'vitest'
import { postLock } from './sessionLock'

afterEach(() => vi.restoreAllMocks())

describe('postLock', () => {
  it('posts acquire with the session id', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: true }) })
    vi.stubGlobal('fetch', fetchMock)
    await postLock('abc', 'acquire')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/remote/lock',
      expect.objectContaining({ method: 'POST' }),
    )
    const body = JSON.parse(fetchMock.mock.calls[0][1].body)
    expect(body).toEqual({ session_id: 'abc', action: 'acquire' })
  })

  it('never throws on network error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('down')))
    await expect(postLock('abc', 'release')).resolves.toBeUndefined()
  })

  it('no-ops without a session id', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    await postLock('', 'acquire')
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
