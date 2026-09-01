import { homedir } from "node:os"
import { join } from "node:path"
import type { Plugin } from "@opencode-ai/plugin"

const NOTIFY_SCRIPT = join(homedir(), ".claude", "scripts", "notify.py")

export const NotifyPlugin: Plugin = async ({ $ }) => {
  return {
    "session.idle": async () => {
      try {
        await $`python3 ${NOTIFY_SCRIPT} --harness "OpenCode" --title "OpenCode" --message "Task completed" --type completed`.quiet()
      } catch {
        // Best-effort -- never block the session on notification failure.
      }
    },
  }
}

export default NotifyPlugin
