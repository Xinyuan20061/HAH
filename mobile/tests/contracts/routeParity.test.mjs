import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const mobileDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
const repoDir = path.resolve(mobileDir, '..')

describe('Android route migration contract', () => {
  it('maps every registered mini-program page to exactly one reachable Android page component', async () => {
    const miniConfig = JSON.parse(await readFile(path.join(repoDir, 'miniprogram/app.json'), 'utf8'))
    const routeMap = JSON.parse(await readFile(path.join(repoDir, 'docs/android/route-map.json'), 'utf8'))
    const router = await readFile(path.join(mobileDir, 'src/router.ts'), 'utf8')

    expect(miniConfig.pages).toHaveLength(28)
    expect(routeMap.routes).toHaveLength(miniConfig.pages.length)
    expect(new Set(routeMap.routes.map(route => route.mini)).size).toBe(routeMap.routes.length)
    expect(new Set(routeMap.routes.map(route => route.android)).size).toBe(routeMap.routes.length)

    const mapped = new Map(routeMap.routes.map(route => [route.mini, route.android]))
    for (const miniPath of miniConfig.pages) expect(mapped.has(miniPath)).toBe(true)

    const registered = new Map()
    for (const match of router.matchAll(/\{\s*path:\s*'([^']+)'\s*,\s*name:\s*'[^']+'\s*,\s*component:\s*([A-Za-z0-9_]+)\s*\}/g)) {
      registered.set(match[1], match[2])
    }
    for (const route of routeMap.routes) {
      const component = registered.get(route.android)
      expect(component, `${route.mini} -> ${route.android}`).toBeTruthy()
      expect(router).toMatch(new RegExp(`import ${component} from './pages/${component}\\.vue'`))
      await expect(readFile(path.join(mobileDir, `src/pages/${component}.vue`), 'utf8')).resolves.toContain('<template')
    }
  })
})
