<script setup lang="ts">
import { RouterLink, RouterView } from 'vue-router'
</script>

<template>
  <el-container class="app-shell">
    <el-header class="app-header">
      <div class="brand">
        <span class="logo">JUJ</span>
        <span class="name">量化回测平台</span>
      </div>

      <nav class="nav">
        <RouterLink to="/" class="nav-item demo">
          模拟回测
          <el-tag
            type="warning"
            size="small"
            effect="plain"
            class="nav-tag"
          >
            合成行情
          </el-tag>
        </RouterLink>
      </nav>
    </el-header>

    <el-main class="app-main">
      <RouterView />
    </el-main>
  </el-container>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
  background: #f5f7fa;
}

.app-header {
  background: #fff;
  border-bottom: 1px solid #ebeef5;
  display: flex;
  align-items: center;
  padding: 0 20px;
}

.brand {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.logo {
  font-size: 18px;
  font-weight: 600;
  color: #409eff;
  letter-spacing: 1px;
}

.name {
  font-size: 15px;
  color: #303133;
}

/* ---------------------------------------------------------
 * 顶部导航
 *
 * 目的：明确标注当前页面使用的是合成行情，
 *       避免用户把模拟结果误解为真实交易表现。
 * --------------------------------------------------------- */

.nav {
  display: flex;
  align-items: center;
  gap: 20px;
  margin-left: 32px;
}

.nav-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  color: #606266;
  text-decoration: none;
  padding-bottom: 2px;
  border-bottom: 2px solid transparent;
  transition: color 0.2s, border-color 0.2s;
}

.nav-item:hover {
  color: #409eff;
}

.nav-item.router-link-exact-active {
  color: #409eff;
  border-bottom-color: #409eff;
}

/* 模拟入口用警示色区分，提醒这是合成数据 */
.nav-item.demo.router-link-exact-active {
  color: #e6a23c;
  border-bottom-color: #e6a23c;
}

.nav-item.demo {
  color: #909399;
}

.nav-tag {
  transform: scale(0.9);
  transform-origin: left center;
}

.app-main {
  padding: 16px 20px 32px;
}
</style>
