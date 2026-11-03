/**
 * Visual evidence for "a note or sketch panel can be expanded" (#21179).
 *
 * WHAT IS FAITHFUL: the REAL MeetingWorkspace and AgentPanel, wired the way
 * MeetingView wires them (one expanded id, siblings hidden), with the real
 * index.css. The transcript is a stand-in block: its own rendering is not under
 * test, only that it gives way while a panel is expanded.
 *
 *   ?expand=none|sketch-artist|note-taker &theme=dark|light
 *
 * Two shells, from website/:
 *   npx vite --host 127.0.0.1 --port 6819 --strictPort
 *   node scripts/capture-meetings-expand-panels.mjs http://127.0.0.1:6819 \
 *     ../temp-screenshots/21179
 */
import { useState } from 'react'
import { createRoot } from 'react-dom/client'

import { initI18n } from '../src/i18n/all'
import AgentPanel from '../src/apps/meetings/components/AgentPanel'
import MeetingWorkspace from '../src/apps/meetings/components/MeetingWorkspace'
import type { AgentDef } from '../src/apps/meetings/api'
import '../src/index.css'

const params = new URLSearchParams(location.search)
const theme = params.get('theme') || 'dark'
const initial = params.get('expand')

document.documentElement.setAttribute('data-theme', theme === 'light' ? 'kiro-light' : 'kiro-dark')

initI18n()

const AGENTS: AgentDef[] = [
  { id: 'note-taker', name: 'Note Taker', widget_type: 'markdown' },
  { id: 'sketch-artist', name: 'Sketch Artist', widget_type: 'html' },
]

const NOTES =
  '# Design review\n\n' +
  Array.from(
    { length: 14 },
    (_, i) => `## Topic ${i + 1}\n\n- Decision recorded for topic ${i + 1}.\n- Owner and due date set.\n`,
  ).join('\n')

const SKETCH = `<h3 style="font-family:sans-serif">Request flow</h3>
<table border="1" cellpadding="6" style="border-collapse:collapse;font-family:sans-serif;font-size:13px">
<tr><th>Step</th><th>Service</th><th>Owner</th><th>Latency</th></tr>
${Array.from({ length: 14 }, (_, i) => `<tr><td>${i + 1}</td><td>service-${i + 1}</td><td>team ${String.fromCharCode(65 + (i % 6))}</td><td>${20 + i * 7} ms</td></tr>`).join('\n')}
</table>`

function Harness() {
  const [expanded, setExpanded] = useState<string | null>(
    AGENTS.some(a => a.id === initial) ? initial : null,
  )
  return (
    <div data-capture-root className="h-screen flex flex-col bg-bg">
      <MeetingWorkspace
        hasAgentPanels
        expanded={expanded !== null}
        agentPanels={
          <div className={expanded !== null ? 'grid grid-cols-1 h-full' : 'grid grid-cols-1 2xl:grid-cols-2 gap-3'}>
            {AGENTS.map(agent => (
              <AgentPanel
                key={agent.id}
                agent={agent}
                output={agent.widget_type === 'html' ? SKETCH : NOTES}
                listening
                chatView={false}
                expanded={expanded === agent.id}
                hidden={expanded !== null && expanded !== agent.id}
                onToggleExpanded={() => setExpanded(c => (c === agent.id ? null : agent.id))}
                onToggleListening={() => undefined}
                onToggleChatView={() => undefined}
                onSendMessage={() => undefined}
              />
            ))}
          </div>
        }
        transcript={
          <div data-capture-transcript className="h-full rounded-xl border border-border p-4 text-[13px] text-muted">
            Transcript
          </div>
        }
      />
    </div>
  )
}

createRoot(document.getElementById('root')!).render(<Harness />)
