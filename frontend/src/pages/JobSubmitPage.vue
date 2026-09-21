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
          label="启动 Actor 流水线"
          :loading="submitting"
          :disable="auth.role !== 'bioops'"
          @click="submit"
        />
      </q-card-actions>
    </q-card>

    <q-dialog v-model="showConfirm" persistent>
      <q-card style="min-width: 380px">
        <q-card-section>
          <div class="text-h6">确认提交</div>
          <div class="text-caption text-grey-7">以下为服务端预检快照</div>
        </q-card-section>
        <q-separator />
        <q-card-section v-if="precheck" class="q-gutter-y-sm">
          <div class="row">
            <div class="col-4 text-grey-7">样例 / 标记</div>
            <div class="col">{{ precheck.sample_name }}</div>
          </div>
          <div class="row">
            <div class="col-4 text-grey-7">文本为空</div>
            <div class="col">
              {{ precheck.text_empty ? '是' : '否' }}（{{ precheck.text_length }} 字符）
            </div>
          </div>
          <div class="row">
            <div class="col-4 text-grey-7">提交人</div>
            <div class="col">{{ precheck.requested_by }}</div>
          </div>
          <q-banner v-if="precheck.text_empty" dense rounded class="bg-warning text-dark">
            预检提示：FASTQ 文本为空，无法入队。
          </q-banner>
        </q-card-section>
        <q-card-actions align="right">
          <q-btn flat label="取消" :disable="confirming" @click="cancelSubmit" />
          <q-btn
            color="primary"
            label="确认入队"
            :loading="confirming"
            :disable="!precheck || precheck.text_empty"
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
import { createJob, listSamples, precheckJob } from '../api/client'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const $q = useQuasar()

const samples = ref([])
const sampleId = ref(null)
const fastqText = ref('')
const submitting = ref(false)

const precheck = ref(null)
const showConfirm = ref(false)
const confirming = ref(false)
let pendingBody = null

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
  return sampleId.value ? { sampleId: sampleId.value } : { fastqText: fastqText.value }
}

async function submit() {
  if (!sampleId.value && !fastqText.value.trim()) {
    $q.notify({ type: 'warning', message: '请选择样例或粘贴 FASTQ 文本' })
    return
  }
  submitting.value = true
  try {
    pendingBody = buildBody()
    precheck.value = await precheckJob(pendingBody)
    showConfirm.value = true
  } catch (e) {
    pendingBody = null
    $q.notify({ type: 'negative', message: e.message || '预检失败' })
  } finally {
    submitting.value = false
  }
}

function cancelSubmit() {
  showConfirm.value = false
  precheck.value = null
  pendingBody = null
}

async function confirmSubmit() {
  if (!pendingBody) return
  confirming.value = true
  try {
    const job = await createJob(pendingBody)
    showConfirm.value = false
    $q.notify({ type: 'positive', message: `作业 #${job.id} 已创建队` })
    router.push(`/jobs/${job.id}`)
  } catch (e) {
    $q.notify({ type: 'negative', message: e.message || '提交失败' })
  } finally {
    confirming.value = false
  }
}

onMounted(load)
</script>
