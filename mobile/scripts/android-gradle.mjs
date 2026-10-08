import { spawnSync } from 'node:child_process'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'
import { findJava21Home } from './java-toolchain.mjs'

const scriptDir = path.dirname(fileURLToPath(import.meta.url))
const mobileDir = path.resolve(scriptDir, '..')
const androidDir = path.join(mobileDir, 'android')
const args = process.argv.slice(2)
if (!args.length || args.some(arg => !/^[A-Za-z0-9:_-]+$/.test(arg))) {
  throw new Error('Pass one or more Gradle task names, such as assembleDebug or assembleRelease.')
}

const javaHome = await findJava21Home()
const env = { ...process.env, JAVA_HOME: javaHome, PATH: `${path.join(javaHome, 'bin')}${path.delimiter}${process.env.PATH || ''}` }
const command = process.platform === 'win32'
  ? (process.env.ComSpec || path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'cmd.exe'))
  : path.join(androidDir, 'gradlew')
const commandArgs = process.platform === 'win32'
  ? ['/d', '/s', '/c', `gradlew.bat ${args.join(' ')}`]
  : args
const result = spawnSync(command, commandArgs, { cwd: androidDir, env, stdio: 'inherit' })
if (result.error) throw result.error
process.exitCode = result.status ?? 1
