import { useGenerateStore } from '../../stores/generateStore'
import StepBar from './StepBar'
import UploadStep from './steps/UploadStep'
import ScriptStep from './steps/ScriptStep'
import MatchingStep from './steps/MatchingStep'
import ConfirmStep from './steps/ConfirmStep'
import PlaceholderStep from './steps/PlaceholderStep'
import styles from './ContentPanel.module.css'

const STEP_MAP = {
  1: UploadStep,
  2: ScriptStep,
  3: MatchingStep,
  4: ConfirmStep,
  5: PlaceholderStep,
}

export default function ContentPanel() {
  const { step } = useGenerateStore()
  const StepComponent = STEP_MAP[step]
  return (
    <div className={styles.panel}>
      <StepBar />
      <div className={styles.content}>
        <StepComponent />
      </div>
    </div>
  )
}
