<script setup lang="ts">
import { reactive, ref } from 'vue'
import { api, errorText } from '../api'
import type { AuthResponse } from '../api'
import AppearanceMenu from './AppearanceMenu.vue'
import NavIcon from './NavIcon.vue'

const emit = defineEmits<{ authenticated: [value: AuthResponse] }>()
const form = reactive({ username: 'admin', password: '' })
const busy = ref(false)
const error = ref('')

async function login() {
  if (busy.value) return
  if (!form.username || !form.password) { error.value = '请填写管理员名和密码。'; return }
  busy.value = true
  error.value = ''
  try {
    const result = await api<AuthResponse>('/auth/login', { method: 'POST', body: JSON.stringify(form) })
    form.password = ''
    emit('authenticated', result)
  } catch (cause) { error.value = errorText(cause) }
  finally { busy.value = false }
}
</script>

<template>
  <main class="login-layout">
    <div class="login-appearance"><AppearanceMenu compact /></div>
    <section class="login-card" aria-labelledby="login-title">
      <h1 id="login-title">登录管理台</h1>
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="form-alert" />
      <el-form :model="form" label-position="top" @submit.prevent="login">
        <el-form-item label="管理员名" for="rp-login-username"><el-input id="rp-login-username" v-model="form.username" autocomplete="username" maxlength="80" size="large" :disabled="busy"><template #prefix><NavIcon name="user" /></template></el-input></el-form-item>
        <el-form-item label="密码" for="rp-login-password"><el-input id="rp-login-password" v-model="form.password" type="password" show-password autocomplete="current-password" maxlength="128" size="large" :disabled="busy"><template #prefix><NavIcon name="lock" /></template></el-input></el-form-item>
        <el-button type="primary" native-type="submit" size="large" :loading="busy" class="full-width login-submit"><span>登录</span><NavIcon name="arrow-right" /></el-button>
      </el-form>
    </section>
  </main>
</template>
