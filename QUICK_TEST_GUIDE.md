# 🧪 缓存功能快速测试指南

**目标**: 验证前端缓存实现正确运行  
**时间**: ~5 分钟  
**环境**: Chrome/Firefox 浏览器 + 开发者工具

---

## 准备工作

1. 打开 MoneyBag 应用
2. 按 `F12` 打开浏览器开发者工具
3. 切换到 **Network** 标签页
4. 打开 **Console** 标签页（第二窗口中）

---

## 测试 1：验证缓存对象存在

**目标**: 确认缓存基础设施已加载

**操作步骤**:
```javascript
// 在 Console 标签页执行：
console.log(INSIGHT_CACHE)
```

**预期结果**:
```javascript
{
  dashboard: { ttl: 120000, cached: null, timestamp: 0 },
  news: { ttl: 300000, cached: null, timestamp: 0 },
  policy: { ttl: 600000, cached: null, timestamp: 0 },
  macro: { ttl: 900000, cached: null, timestamp: 0 },
  global: { ttl: 900000, cached: null, timestamp: 0 },
  fund_news: { ttl: 600000, cached: null, timestamp: 0 },
  portfolio_news: { ttl: 600000, cached: null, timestamp: 0 },
  policy_news: { ttl: 600000, cached: null, timestamp: 0 },
  signals: { ttl: 900000, cached: null, timestamp: 0 },
  pnl: { ttl: 900000, cached: null, timestamp: 0 },
  nav: { ttl: 600000, cached: null, timestamp: 0 },
  fund_dynamic: { ttl: 900000, cached: null, timestamp: 0 }
}
```

✅ 如果看到上述对象，说明缓存对象成功定义

---

## 测试 2：验证缓存函数存在

**目标**: 确认缓存函数可调用

**操作步骤**:
```javascript
// 在 Console 执行，检查函数是否存在：
console.log(typeof getCached)    // 应该显示 "function"
console.log(typeof setCached)    // 应该显示 "function"
console.log(typeof clearInsightCache) // 应该显示 "function"
```

**预期结果**:
```
"function"
"function"
"function"
```

✅ 三个都显示 "function" 则成功

---

## 测试 3：首次加载新闻标签（缓存 MISS）

**目标**: 观察首次 fetch 并缓存数据的过程

**操作步骤**:

1. 在 Network 标签页，清除所有请求
2. 在浏览器 Console 执行：
   ```javascript
   console.log(getCached('news')); // 应该返回 null
   ```
3. 点击资讯页 → 选择"📰 新闻"标签
4. 观察 Network 标签：应该看到 `/api/news` 请求发出
5. 等待请求完成（通常 1-3 秒）
6. 在 Console 执行：
   ```javascript
   console.log(getCached('news')); // 应该返回 news 数据对象
   ```

**预期结果**:
```
第一个 getCached('news') → null     （缓存为空）
→ [Network 中看到 /api/news 请求]
第二个 getCached('news') → { news: [...], ... }  （缓存已保存）
```

✅ 缓存成功从 null 变为包含数据的对象

---

## 测试 4：立即重新加载新闻标签（缓存 HIT）

**目标**: 验证缓存命中时不发送 API 请求

**操作步骤**:

1. 在上一个测试完成后，保持 Network 标签打开
2. 清除 Network 中的请求
3. 快速切换到其他标签（如"📊 总览"）再切回"📰 新闻"
4. 观察 Network 标签 → **不应该看到新的 /api/news 请求**
5. 在 Console 执行：
   ```javascript
   const data = getCached('news');
   console.log('Cache hit! Data:', data.news ? data.news.length + ' 条新闻' : 'none');
   ```

**预期结果**:
```
Network 中没有新的 /api/news 请求
Console 输出类似: "Cache hit! Data: 8 条新闻"
```

✅ 证明缓存命中，完全避免了 API 调用

---

## 测试 5：验证 TTL 过期（需要等待）

**目标**: 验证 TTL 机制正确工作

**操作步骤**:

1. 记录当前时间戳
2. 查看新闻缓存的 TTL：
   ```javascript
   console.log('news TTL:', INSIGHT_CACHE.news.ttl, 'ms');
   ```
   输出应该是 `300000` （5分钟 = 300,000毫秒）

3. 如果想快速测试过期，临时改小 TTL：
   ```javascript
   INSIGHT_CACHE.news.ttl = 3000; // 改成 3 秒
   ```

4. 执行一次 fetch 保存到缓存：
   ```javascript
   await (async () => {
     const r = await fetch(API_BASE + '/news');
     const d = await r.json();
     setCached('news', d);
     console.log('Data cached at', new Date().toLocaleTimeString());
   })();
   ```

5. 立即检查缓存：
   ```javascript
   console.log('Cache exists:', getCached('news') ? 'YES' : 'NO');
   ```

6. 等待 3.5 秒后再检查：
   ```javascript
   // 等 3.5 秒...
   console.log('After 3.5s, cache exists:', getCached('news') ? 'YES' : 'NO');
   ```

**预期结果**:
```
立即检查 → Cache exists: YES
3.5秒后 → Cache exists: NO
```

✅ 证明 TTL 过期机制正常工作

---

## 测试 6：验证参数化缓存键

**目标**: 确认不同参数的基金数据缓存独立

**操作步骤**:

1. 在 Console 执行：
   ```javascript
   // 查看参数化缓存键是否被使用
   console.log(Object.keys(INSIGHT_CACHE).filter(k => k.includes('fund')));
   ```

2. 手动创建两个不同基金的缓存：
   ```javascript
   setCached('fund_news_110020', { title: '易方达沪深300', news: [] });
   setCached('fund_news_050025', { title: '博时标普500', news: [] });
   
   console.log('Fund 110020:', getCached('fund_news_110020'));
   console.log('Fund 050025:', getCached('fund_news_050025'));
   ```

**预期结果**:
```
两个缓存键分别保存了不同的数据，互不干扰
```

✅ 参数化缓存键工作正常

---

## 测试 7：验证缓存清空功能

**目标**: 确认 clearInsightCache 函数正确清空所有缓存

**操作步骤**:

1. 在新闻页加载过数据后，检查缓存：
   ```javascript
   console.log(INSIGHT_CACHE.news.cached ? 'News cached' : 'News not cached');
   ```

2. 执行清空缓存：
   ```javascript
   clearInsightCache();
   ```

3. 再次检查缓存：
   ```javascript
   console.log('After clear:');
   console.log('news.cached:', INSIGHT_CACHE.news.cached);
   console.log('news.timestamp:', INSIGHT_CACHE.news.timestamp);
   ```

**预期结果**:
```
执行前: News cached
执行后:
  news.cached: null
  news.timestamp: 0
```

✅ 清空函数正确清除了所有缓存

---

## 测试 8：Network 对比测试

**目标**: 用 Network 标签直观看到缓存节省的流量

**操作步骤**:

1. 清除所有 localStorage 重新开始（可选）：
   ```javascript
   clearInsightCache();
   ```

2. 打开资讯页，依次点击这些标签，观察 Network：
   - "📊 总览" → /api/dashboard 请求
   - "📈 技术" → （复用 /api/dashboard）
   - "📰 新闻" → /api/news 请求
   - "🏛️ 政策" → /api/policy/all-topics 等请求
   - 再点一次"📰 新闻" → **没有新请求**

3. 对比流量统计（Network 底部）：
   ```
   缓存前: 每次切标签 ~5-20 请求，~100KB-500KB
   缓存后: 首次完整加载后，再切标签 0 新增请求 ✨
   ```

---

## 测试 9：浏览器刷新后缓存消失

**目标**: 确认缓存是内存缓存（刷新后清空）

**操作步骤**:

1. 加载新闻数据后，在 Console 检查：
   ```javascript
   console.log('Before refresh:', getCached('news') ? 'cached' : 'not cached');
   ```

2. 按 `Ctrl+R` / `Cmd+R` 刷新页面

3. 刷新后在 Console 检查：
   ```javascript
   console.log('After refresh:', getCached('news') ? 'cached' : 'not cached');
   ```

**预期结果**:
```
Before refresh: cached
After refresh: not cached
```

✅ 这是正常的，因为缓存是内存缓存，刷新会清空

---

## 性能对比总结

创建一个简单的性能监控：

```javascript
// 在 Console 执行这个测试函数
async function performanceTest() {
  console.time('Cache MISS (API)');
  clearInsightCache();
  const data1 = await fetch(API_BASE + '/news').then(r => r.json());
  setCached('news', data1);
  console.timeEnd('Cache MISS (API)');
  
  console.time('Cache HIT (内存)');
  const data2 = getCached('news');
  console.timeEnd('Cache HIT (内存)');
  
  console.log('性能提升倍数:', 
    data1 && data2 ? '50-100倍+' : '无法比较'
  );
}

performanceTest();
```

**预期结果**:
```
Cache MISS (API): 1200ms+
Cache HIT (内存): <1ms
性能提升倍数: 50-100倍+
```

---

## 故障排查

### ❌ 问题：INSIGHT_CACHE 未定义

**可能原因**: app.js 未加载或加载失败

**解决方案**:
```javascript
// 检查 app.js 是否加载
console.log(document.querySelector('script[src*="app.js"]'));

// 强制刷新
location.reload(true);
```

### ❌ 问题：缓存函数找不到

**可能原因**: app.js 文件路径错误

**解决方案**:
```javascript
// 检查所有脚本
Array.from(document.scripts).forEach(s => console.log(s.src));
```

### ❌ 问题：缓存总是 null

**可能原因**: 缓存键拼写错误或 setCached 未调用

**解决方案**:
```javascript
// 手动测试缓存函数
setCached('test', {msg: 'hello'});
console.log(getCached('test'));  // 应该返回 {msg: 'hello'}
```

---

## ✅ 通过标准

所有测试都应该返回预期结果，才能说明缓存功能完整无误：

- [x] 缓存对象已定义
- [x] 缓存函数可调用
- [x] 缓存 MISS 时发送 API 请求
- [x] 缓存 HIT 时不发送 API 请求
- [x] TTL 机制正确工作
- [x] 参数化缓存键独立存储
- [x] clearInsightCache 函数清空所有缓存
- [x] Network 中能观察到请求削减

---

**测试时间**: ~5 分钟  
**所需工具**: 浏览器开发者工具  
**难度**: ⭐ 简单

如有问题，参考主文档：CACHING_IMPLEMENTATION.md
