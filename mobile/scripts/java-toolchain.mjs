import { readdir } from 'node:fs/promises'
import { spawnSync } from 'node:child_process'
import path from 'node:path'
import process from 'node:process'

function javaExecutable(home) {
  return path.join(home, 'bin', process.platform === 'win32' ? 'java.exe' : 'java')
}

export function getJavaVersion(home) {
  const result = spawnSync(javaExecutable(home), ['-version'], { encoding: 'utf8' })
  if (result.error || result.status !== 0) return ''
  return `${result.stderr || ''}\n${result.stdout || ''}`.match(/version "([^"]+)"/)?.[1] || ''
}

async function listChildren(parent, pattern) {
  try {
    return (await readdir(parent, { withFileTypes: true }))
      .filter(entry => entry.isDirectory() && pattern.test(entry.name))
      .map(entry => path.join(parent, entry.name))
  } catch {
    return []
  }
}

async function commonJdkHomes() {
  const candidates = []
  if (process.platform === 'win32') {
    const local = process.env.LOCALAPPDATA
    const localAdoptium = local && path.join(local, 'Programs', 'HealthMate-JDK21', 'PFiles64', 'Eclipse Adoptium')
    const programFiles = [process.env.ProgramFiles, process.env['ProgramFiles(x86)']].filter(Boolean)
    for (const parent of [localAdoptium, ...programFiles.map(dir => path.join(dir, 'Eclipse Adoptium')), ...programFiles.map(dir => path.join(dir, 'Java'))]) {
      if (parent) candidates.push(...await listChildren(parent, /(?:jdk|temurin)[-_]?21/i))
    }
  } else {
    for (const parent of ['/usr/lib/jvm', '/opt/java', '/opt']) {
      candidates.push(...await listChildren(parent, /(?:jdk|java|temurin)[-_]?21/i))
    }
  }
  return candidates
}

export async function findJava21Home() {
  const candidates = [
    process.env.ANDROID_JAVA21_HOME,
    process.env.JAVA_HOME,
    ...(await commonJdkHomes()),
  ].filter((value, index, values) => value && values.indexOf(value) === index)

  for (const home of candidates) {
    if (/^21(?:\.|$)/.test(getJavaVersion(home))) return home
  }
  throw new Error('Android plugins require JDK 21. Set JAVA_HOME or ANDROID_JAVA21_HOME to a JDK 21 installation.')
}
