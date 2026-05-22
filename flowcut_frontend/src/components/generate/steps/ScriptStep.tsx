import { useState } from 'react'
import { useGenerateStore } from '../../../stores/generateStore'
import styles from './Step.module.css'

export default function ScriptStep() {
  const {
    scripts,
    selectedScriptId,
    selectScript,
    addMessage,
    setAgentTyping,
    setStep,
    runMaterialMatch,
    matchLoading,
  } = useGenerateStore()
  const [expanded, setExpanded] = useState<string | null>(selectedScriptId)

  const handleConfirm = async () => {
    const sel = scripts.find((s) => s.id === selectedScriptId)
    if (!sel) return
    addMessage({ role: 'user', type: 'text', content: `确认脚本：${sel.name}` })
    addMessage({
      role: 'agent',
      type: 'progress',
      content: '',
      label: '正在匹配素材库…',
      subLabel: '双向量语义检索（产品专属 + 通用兜底）',
      done: false,
    })
    setAgentTyping(true)
    const ok = await runMaterialMatch()
    setAgentTyping(false)
    if (ok) setStep(3)
  }

  return (
    <div className={styles.wrap}>
      <div className={styles.title}>选择脚本</div>
      <div className={styles.sub}>已基于爆款视频生成 {scripts.length} 条差异化脚本，点击展开分镜详情，选择一条继续。</div>
      <div className={styles.scriptList}>
        {scripts.map((sc) => {
          const isSel = selectedScriptId === sc.id
          const isExp = expanded === sc.id
          return (
            <div
              key={sc.id}
              className={`${styles.scriptCard} ${isSel ? styles.selected : ''}`}
              onClick={() => { selectScript(sc.id); setExpanded(sc.id) }}
            >
              <div className={styles.scHead}>
                <div className={`${styles.scNum} ${isSel ? styles.scNumSel : ''}`}>{sc.id.replace('s', '')}</div>
                <div className={styles.scInfo}>
                  <div className={styles.scName}>{sc.name}</div>
                  <div className={styles.scHook}>{sc.hook}</div>
                </div>
                <div className={styles.scRight}>
                  {isSel && <span className={styles.selBadge}>已选</span>}
                  <div className={styles.tags}>
                    <span className={styles.tagBlue}>{sc.scenes.length}段</span>
                    <span className={styles.tagGray}>{sc.durationSec}s</span>
                  </div>
                </div>
              </div>
              {isExp && sc.scenes.length > 0 && (
                <div className={styles.scDetail}>
                  {sc.scenes.map((scene, i) => (
                    <div key={i} className={styles.scene}>
                      <span className={styles.sceneTime}>{scene.startSec.toFixed(1)}s – {scene.endSec.toFixed(1)}s</span>
                      <span className={styles.sceneDesc}>
                        <span className={scene.category === '真人口播' ? styles.catSpokesperson : styles.catProduct}>{scene.category}</span>
                        <strong>{scene.label}</strong>：{scene.description}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>
      <button
        className={styles.actionBtn}
        onClick={handleConfirm}
        disabled={!selectedScriptId || matchLoading}
      >
        {matchLoading ? '匹配中…' : '确认脚本，开始匹配素材 →'}
      </button>
    </div>
  )
}
