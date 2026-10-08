import { createHash } from 'node:crypto'
import { copyFile, mkdir, readFile, rm, writeFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import os from 'node:os'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'
import { loadEnv } from 'vite'
import { findJava21Home, getJavaVersion } from './java-toolchain.mjs'

const scriptDir = path.dirname(fileURLToPath(import.meta.url))
const mobileDir = path.resolve(scriptDir, '..')
const repoDir = path.resolve(mobileDir, '..')
const apkPath = path.join(mobileDir, 'android', 'app', 'build', 'outputs', 'apk', 'debug', 'app-debug.apk')
const gradleFile = path.join(mobileDir, 'android', 'app', 'build.gradle')
const gradleSource = await readFile(gradleFile, 'utf8')
const versionCode = Number(gradleSource.match(/versionCode\s+(\d+)/)?.[1] || 0)
const versionName = gradleSource.match(/versionName\s+["']([^"']+)["']/)?.[1]
const releaseApplicationId = process.env.ANDROID_RELEASE_APPLICATION_ID?.trim()
const applicationId = `${releaseApplicationId || 'com.hah.healthmate'}.dev`
if (!versionCode || !versionName) throw new Error('Android package metadata is incomplete.')

const sdkRoot = process.env.ANDROID_HOME || process.env.ANDROID_SDK_ROOT
  || path.join(os.homedir(), 'AppData', 'Local', 'Android', 'Sdk')
const buildToolsDir = path.join(sdkRoot, 'build-tools', '36.0.0')
const apksigner = path.join(buildToolsDir, process.platform === 'win32' ? 'apksigner.bat' : 'apksigner')
const apksignerJar = path.join(buildToolsDir, 'lib', 'apksigner.jar')
const aapt = path.join(buildToolsDir, process.platform === 'win32' ? 'aapt.exe' : 'aapt')

const outputDir = path.join(repoDir, 'dist', 'android', versionName)
const apkName = `HAH-${versionName}-debug.apk`
const outputApk = path.join(outputDir, apkName)
await mkdir(outputDir, { recursive: true })
await copyFile(apkPath, outputApk)

const apkBytes = await readFile(outputApk)
const apkSha256 = createHash('sha256').update(apkBytes).digest('hex')
const signerOutput = process.platform === 'win32'
  ? execFileSync('java', ['-jar', apksignerJar, 'verify', '--print-certs', outputApk], { encoding: 'utf8' })
  : execFileSync(apksigner, ['verify', '--print-certs', outputApk], { encoding: 'utf8' })
const certificateSha256 = signerOutput.match(/Signer #1 certificate SHA-256 digest:\s*([a-f0-9:]+)/i)?.[1]
if (!certificateSha256) throw new Error('Could not read the APK signing certificate fingerprint.')
// Android's legacy aapt.exe uses the active ANSI code page for paths on Windows.
// Stage a temporary ASCII-path copy so builds under non-Latin workspace paths work.
const aaptInput = path.join(os.tmpdir(), `hah-mobile-${process.pid}.apk`)
await copyFile(outputApk, aaptInput)
let packageOutput
try {
  packageOutput = execFileSync(aapt, ['dump', 'badging', aaptInput], { encoding: 'utf8' })
} finally {
  await rm(aaptInput, { force: true })
}
const actualApplicationId = packageOutput.match(/^package: name='([^']+)'/m)?.[1]
if (actualApplicationId !== applicationId) throw new Error('APK package ID does not match the Android build configuration.')

const gradleWrapper = await readFile(path.join(mobileDir, 'android', 'gradle', 'wrapper', 'gradle-wrapper.properties'), 'utf8')
const gradleVersion = gradleWrapper.match(/gradle-(\d+(?:\.\d+)+)-/)?.[1] || 'unknown'
const packageJson = JSON.parse(await readFile(path.join(mobileDir, 'package.json'), 'utf8'))
const javaHome = await findJava21Home()
const javaVersion = getJavaVersion(javaHome)
const publicEnv = loadEnv('development', mobileDir, 'VITE_')
const gitCommit = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: repoDir, encoding: 'utf8' }).trim()
const gitStatus = execFileSync('git', ['status', '--porcelain'], { cwd: repoDir, encoding: 'utf8' })
const manifest = {
  commit: gitCommit,
  working_tree_dirty: Boolean(gitStatus.trim()),
  version_name: versionName,
  version_code: versionCode,
  application_id: applicationId,
  build_type: 'debug',
  public_environment: {
    api_base_url_configured: Boolean(publicEnv.VITE_API_BASE_URL?.trim()),
    cloudbase_env_id_configured: Boolean(publicEnv.VITE_CLOUDBASE_ENV_ID?.trim()),
    wechat_mobile_app_id_configured: Boolean(process.env.WECHAT_MOBILE_APP_ID?.trim()),
  },
  toolchain: {
    node: process.version,
    java: javaVersion,
    gradle: gradleVersion,
    android_build_tools: '36.0.0',
  },
  dependencies: packageJson.dependencies,
  dev_dependencies: packageJson.devDependencies,
  build_time_utc: new Date().toISOString(),
  apk_sha256: apkSha256,
  signing_certificate_sha256: certificateSha256,
}

await writeFile(path.join(outputDir, 'build-manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`, 'utf8')
await writeFile(path.join(outputDir, 'checksums.sha256'), `${apkSha256}  ${apkName}\n`, 'utf8')
await writeFile(path.join(outputDir, 'installation.md'), `# HealthMate ${versionName} Debug 安装说明\n\n- 包名：\`${applicationId}\`\n- 安装：在已连接设备并启用 USB 调试后运行 \`adb install -r ${apkName}\`。\n- 本包为开发版；微信开放平台、后端 HTTPS 地址与媒体存储需按开发环境配置。\n- 覆盖安装要求设备上已有相同包名及兼容签名。\n`, 'utf8')
await writeFile(path.join(outputDir, 'permissions.md'), `# Android 权限说明\n\n- 网络访问（INTERNET）：连接微信云托管 API、语音识别/合成服务及用户授权的 COS/S3 媒体地址。\n- 麦克风（RECORD_AUDIO）：仅在用户主动开始语音输入时录制；录音在本机缓存中暂存，上传识别后删除，切到后台或取消时丢弃。\n- 相机和相册：通过系统相机/照片选择器获取用户主动选择的照片或视频，不申请整库媒体访问权限。\n`, 'utf8')
await writeFile(path.join(outputDir, 'release-notes.md'), `# HealthMate ${versionName} Debug\n\n包含 Android 原生微信登录接入、Keystore 会话保护、语音输入/播报、餐食识别、动作视频分析、健康记录与计划确认、账号关联及隐私管理页面。该包用于开发验证，不是正式签名发布包。\n`, 'utf8')

process.stdout.write(`Packaged ${outputApk}\nSHA-256 ${apkSha256}\n`)
