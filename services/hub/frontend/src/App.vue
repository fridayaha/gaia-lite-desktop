<template>
  <div id="app">
    <el-container style="min-height:100vh">
      <el-header>
        <div style="display:flex;align-items:center;gap:24px;width:100%">
          <h2 style="margin:0;font-size:18px">Hub 能力资产管理</h2>
          <router-link to="/items" style="color:#d9ecff;text-decoration:none;font-size:14px">能力列表</router-link>
          <router-link to="/runtime" style="color:#d9ecff;text-decoration:none;font-size:14px">Runtime 调试</router-link>
        </div>
      </el-header>
      <el-main>
        <router-view />
      </el-main>
    </el-container>
  </div>
</template>

<style>
html, body, #app {
  margin: 0;
  padding: 0;
  width: 100%;
  min-height: 100vh;
  font-family: 'Helvetica Neue', Arial, sans-serif;
  background: #f5f7fa;
}
#app .el-header {
  background: #409eff;
  color: #fff;
  display: flex;
  align-items: center;
  padding: 0 24px;
  height: 56px;
}
#app .el-header h2 { margin: 0; font-size: 18px; }
#app .el-main {
  padding: 24px;
  width: 100%;
  box-sizing: border-box;
}
</style>
