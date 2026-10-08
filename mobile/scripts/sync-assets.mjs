import { copyFile, mkdir, readdir, readFile, writeFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import { dirname, join, relative, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const sourceRoot = join(root, 'miniprogram', 'assets')
const outputRoot = join(root, 'mobile', 'public', 'generated-assets')
const manifestPath = join(outputRoot, 'manifest.json')
const checkOnly = process.argv.includes('--check')

async function listFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true })
  const nested = await Promise.all(entries.map(async entry => {
    const absolute = join(directory, entry.name)
    if (entry.isDirectory()) return listFiles(absolute)
    return [absolute]
  }))
  return nested.flat().sort((a, b) => a.localeCompare(b, 'en'))
}

const assets = []
for (const source of await listFiles(sourceRoot)) {
  const path = relative(sourceRoot, source).split(sep).join('/')
  const bytes = await readFile(source)
  const sha256 = createHash('sha256').update(bytes).digest('hex')
  assets.push({ path, bytes: bytes.byteLength, sha256 })
  const destination = join(outputRoot, ...path.split('/'))
  if (checkOnly) {
    const existing = await readFile(destination).catch(() => null)
    if (!existing || createHash('sha256').update(existing).digest('hex') !== sha256) {
      throw new Error(`Generated asset is missing or differs from source: ${path}`)
    }
  } else {
    await mkdir(dirname(destination), { recursive: true })
    await copyFile(source, destination)
  }
}

const manifest = `${JSON.stringify({ source: 'miniprogram/assets', assets }, null, 2)}\n`
if (checkOnly) {
  const existingManifest = await readFile(manifestPath, 'utf8').catch(() => '')
  if (existingManifest !== manifest) throw new Error('Asset manifest is missing or out of date')
} else {
  await writeFile(manifestPath, manifest, 'utf8')
}

process.stdout.write(`${checkOnly ? 'Verified' : 'Synced'} ${assets.length} source assets.\n`)
