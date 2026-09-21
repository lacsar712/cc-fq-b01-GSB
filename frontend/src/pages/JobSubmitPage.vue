<template>
  <q-page class="page-pad">
    <div class="text-h5 q-mb-md">提交质控作业</div>

    <q-banner v-if="auth.role !== 'bioops'" class="bg-warning text-dark q-mb-md" rounded>
      审计员不可提交作业，请使用 bioops 账号。
    </q-banner>

    <q-card flat bordered>
      <q-card-section>
        <div class="text-subtitle1 q-mb-sm">方式一：选择 seed 样例</div>
        <q-select
          v-model="sampleId"
          :options="sampleOptions"
          label="样例"
          outlined
          dense
          clearable
          emit-value
          map-options
          class="q-mb-lg"
        />

        <div class="text-subtitle1 q-mb-sm">方式二：粘贴 FASTQ 文本</div>
        <q-input
          v-model="fastqText"
          type="textarea"
          outlined
          autogrow
          :input-style="{ minHeight: '160px', fontFamily: 'monospace' }"
          hint="四行一组：@header / 序列 / + / 质量串。若已选样例则优先用样例。"
        />
      </q-card-section>
      <q-card-actions align="right">
        <q-btn flat label="取消" to="/samples" />
        <q-btn
          color="primary"
          label="预检并提交"
          :loading="preflighting"
          @click="requestPreflight"
        />
      </q-card-actions>
    </q-card>

    <!-- 确认对话框：只渲染服务端预检快照，取消不创建，确认后才入队 -->
    <q-dialog v-model="dialogVisible" persistent>
      <q-card v-if="snapshot" class="preflight-card">
        <q-card-section class="row items-center q-pb-none">
          <div class="text-h6">提交前预检快照</div>
          <q-space />
          <q-btn v-close-popup flat round dense icon="close" :disable="confirming" />
        </q-card-section>

        <q-card-section>
          <q-list dense>
            <q-item>
              <q-item-section>
                <q-item-label caption>来源</q-item-label>
                <q-item-label class="text-body1">
                  <q-badge v-if="!snapshot.is_custom" color="primary">样例库</q-badge>
                  <q-badge v-else color="secondary">自定义输入</q-badge>
                </q-item-label>
              </q-item-section>
            </q-item>
            <q-item>
              <q-item-section>
                <q-item-label caption>{{ snapshot.is_custom ? '自定义标记' : '样例名' }}</q-item-label>
                <q-item-label class="text-body1 mono">{{ snapshot.sample_name }}</q-item-label>
              </q-item-section>
            </q-item>
            <q-item>
              <q-item-section>
                <q-item-label caption>文本是否为空（粗检）</q-item-label>
                <q-item-label class="text-body1">
                  <q-badge :color="snapshot.text_empty ? 'negative' : 'positive'">
                    {{ snapshot.text_empty ? '空文本' : `非空（${snapshot.text_length} 字符）` }}
                  </q-badge>
                </q-item-label>
              </q-item-section>
            </q-item>
            <q-item>
              <q-item-section>
                <q-item-label caption>提交人（当前用户）</q-item-label>
                <q-item-label class="text-body1 mono">
                  {{ snapshot.username }}（{{ snapshot.role }}）
                </q-item-label>
              </q-item-section>
            </q-item>
          </q-list>

          <q-banner
            v-if="snapshot.text_empty"
            class="bg-negative text-white q-mt-sm"
            rounded
            dense
          >
            快照显示文本为空，无法提交。
          </q-banner>
        </q-card-section>

        <q-card-actions align="right">
          <q-btn flat label="取消" :disable="confirming" v-close-popup />
          <q-btn
            color="primary"
            label="确认入队"
            :disable="snapshot.text_empty"
            :loading="confirming"
            @click="confirmSubmit"
          />
        </q-card-actions>
      </q-card>
    </q-dialog>
  </q-page>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useQuasar } from 'quasar'
import { createJob, listSamples, preflightJob } from '../api/client'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const $q = useQuasar()

const samples = ref([])
const sampleId = ref(null)
const fastqText = ref('')
const preflighting = ref(false)
const confirming = ref(false)
const dialogVisible = ref(false)
const snapshot = ref(null)

const sampleOptions = computed(() =>
  samples.value.map((s) => ({
    label: `${s.name}（${s.is_broken ? '损坏' : '合格'}）`,
    value: s.id,
  })),
)

async function load() {
  try {
    samples.value = await listSamples()
    const q = route.query.sampleId
    if (q) {
      sampleId.value = Number(q)
    }
  } catch (e) {
    $q.notify({ type: 'negative', message: e.message || '加载样例失败' })
  }
}

function buildBody() {
  return sampleId.value
    ? { sampleId: sampleId.value }
    : { fastqText: fastqText.value }
}

// 第一步：拉服务端预检快照，对话框只渲染该快照
async function requestPreflight() {
  if (!sampleId.value && !fastqText.value.trim()) {
    $q.notify({ type: 'warning', message: '请选择样例或粘贴 FASTQ 文本' })
    return
  }
  preflighting.value = true
  try {
    snapshot.value = await preflightJob(buildBody())
    dialogVisible.value = true
  } catch (e) {
    $q.notify({ type: 'negative', message: e.message || '预检失败' })
  } finally {
    preflighting.value = false
  }
}

// 第二步：确认后才真正 POST /jobs 入队；取消仅关闭对话框，无任何创建
async function confirmSubmit() {
  confirming.value = true
  try {
    const job = await createJob(buildBody())
    dialogVisible.value = false
    $q.notify({ type: 'positive', message: `作业 #${job.id} 已创建并入队` })
    router.push(`/jobs/${job.id}`)
  } catch (e) {
    $q.notify({ type: 'negative', message: e.message || '提交失败' })
  } finally {
    confirming.value = false
  }
}

onMounted(load)
</script>
