import { readFile, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const repositoryRoot = resolve(here, '../..')
const miniConfig = JSON.parse(await readFile(resolve(repositoryRoot, 'miniprogram/app.json'), 'utf8'))
const mapPath = resolve(repositoryRoot, 'docs/android/route-map.json')
const existing = JSON.parse(await readFile(mapPath, 'utf8'))
const routesBySource = new Map(existing.routes.map(route => [route.mini, route]))
const routes = miniConfig.pages.map(mini => {
  const route = routesBySource.get(mini)
  if (!route) throw new Error(`Android route mapping is missing: ${mini}`)
  return route
})

const currentSources = new Set(miniConfig.pages)
const stale = existing.routes.filter(route => !currentSources.has(route.mini))
if (stale.length) {
  throw new Error(`Remove or replace stale mini-program routes in route-map.json: ${stale.map(item => item.mini).join(', ')}`)
}

const generated = `${JSON.stringify({
  source: 'miniprogram/app.json',
  sourceCommit: 'generated-from-current-worktree',
  routes,
}, null, 2)}\n`
await writeFile(mapPath, generated, 'utf8')
process.stdout.write(`Updated Android route map from ${routes.length} mini-program routes.\n`)
