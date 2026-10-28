import { createRouter, createWebHashHistory } from 'vue-router'

import DemoBacktestView from '@/views/DemoBacktestView.vue'

const router = createRouter({
  // 使用 hash 模式而非 history 模式：
  //   hash 模式不需要服务端做 history 回退配置，
  //   静态托管（GitHub Pages 等）与本地预览都能直接打开。
  history: createWebHashHistory(),
  routes: [
    {
      path: '/',
      name: 'demo',
      component: DemoBacktestView,
      meta: { title: '模拟回测（合成行情）' },
    },
    {
      // 必须放在兜底路由之前，
      // 否则会被 :pathMatch(.*)* 抢先匹配并静默跳回首页。
      path: '/demo',
      name: 'demo-alias',
      component: DemoBacktestView,
      meta: { title: '模拟回测（合成行情）' },
    },
    {
      path: '/:pathMatch(.*)*',
      redirect: '/',
    },
  ],
})

export default router
