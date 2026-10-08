import { publicRequest } from './api'

export async function loginDev(nickname: string): Promise<{ access_token: string; user: { id: number; nickname: string } }> {
  return publicRequest('/auth/dev-login', {
    method: 'POST',
    body: JSON.stringify({ nickname }),
  })
}
