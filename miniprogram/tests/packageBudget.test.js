'use strict'
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const root = path.resolve(__dirname, '..')
const outer = JSON.parse(fs.readFileSync(path.resolve(root, '..', 'project.config.json'), 'utf8'))
const inner = JSON.parse(fs.readFileSync(path.join(root, 'project.config.json'), 'utf8'))

function sourceFiles(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap(entry => {
    const full = path.join(dir, entry.name)
    return entry.isDirectory() ? sourceFiles(full) : [full]
  })
}

test('两种导入方式的打包排除规则一致，且源码留有 2 MB 余量', () => {
  assert.deepEqual(outer.packOptions.ignore, inner.packOptions.ignore)
  const ignored = outer.packOptions.ignore
  const files = sourceFiles(root)
  const excluded = file => {
    const relative = path.relative(root, file).split(path.sep).join('/')
    return ignored.some(rule => rule.type === 'file' ? relative === rule.value
      : (relative === rule.value || relative.startsWith(rule.value + '/')))
  }
  assert.ok(files.some(excluded), '排除规则应实际命中源码文件')
  const keptBytes = files.filter(file => !excluded(file)).reduce((sum, file) => sum + fs.statSync(file).size, 0)
  assert.ok(keptBytes < 1.8 * 1024 * 1024, `未压缩源码 ${(keptBytes / 1024).toFixed(1)} KiB，余量不足`)
  const runtimeFiles = files.filter(file => !excluded(file) && /\.(js|json|wxml|wxss)$/.test(file))
  const runtimeSource = runtimeFiles.map(file => fs.readFileSync(file, 'utf8')).join('\n')
  for (const rule of ignored.filter(rule => rule.type === 'file' && rule.value.startsWith('assets/'))) {
    assert.ok(!runtimeSource.includes('/' + rule.value), `被排除素材仍被运行时代码引用：${rule.value}`)
  }
})
