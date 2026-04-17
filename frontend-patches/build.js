#!/usr/bin/env node
/**
 * V6 前端欠账补丁合并脚本
 * 将 frontend-patches/v6/*.js 按编号顺序拼接到 app.js 末尾
 * 用法：node frontend-patches/build.js
 */
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const APP_JS = path.join(ROOT, 'app.js');
const PATCHES_DIR = path.join(__dirname, 'v6');

// 读取 app.js
let appContent = fs.readFileSync(APP_JS, 'utf-8');

// 检查是否已经打过补丁
const MARKER_START = '\n// ===== V6 FRONTEND PATCHES START =====';
const MARKER_END = '// ===== V6 FRONTEND PATCHES END =====';

if (appContent.includes(MARKER_START)) {
  // 已有补丁 → 先剥离旧补丁
  const startIdx = appContent.indexOf(MARKER_START);
  const endIdx = appContent.indexOf(MARKER_END);
  if (endIdx > startIdx) {
    appContent = appContent.slice(0, startIdx) + appContent.slice(endIdx + MARKER_END.length);
    console.log('🔄 已剥离旧 V6 补丁');
  }
}

// 收集补丁文件并按编号排序
const files = fs.readdirSync(PATCHES_DIR)
  .filter(f => f.endsWith('.js'))
  .sort();

console.log('📦 合并以下补丁：');
let patchContent = '';
files.forEach(f => {
  console.log('  →', f);
  patchContent += '\n// --- ' + f + ' ---\n';
  patchContent += fs.readFileSync(path.join(PATCHES_DIR, f), 'utf-8');
});

// 拼接
const finalPatch = MARKER_START + '\n' + patchContent + '\n' + MARKER_END + '\n';
appContent = appContent.trimEnd() + '\n' + finalPatch;

fs.writeFileSync(APP_JS, appContent, 'utf-8');

const lines = appContent.split('\n').length;
console.log(`✅ app.js 合并完成（${lines} 行）`);
console.log('🔍 请运行 node --check app.js 验证语法');
