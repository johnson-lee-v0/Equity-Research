import { Html, OrbitControls, RoundedBox } from '@react-three/drei'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import * as THREE from 'three'
import type { AgentRecord, OutputRecord, RunDetail } from '../types'

export type OfficePoint = { x: number; y: number; z: number; room: string; kind: 'agent' | 'room' }

const POINTS: Record<string, OfficePoint> = {
  A00: { x: 0, y: 0.25, z: 5.05, room: 'Reception', kind: 'agent' },
  A01: { x: -8.7, y: 0.25, z: 0.05, room: 'Analyst floor', kind: 'agent' },
  A02: { x: -3.1, y: 0.25, z: 0.05, room: 'Analyst floor', kind: 'agent' },
  A03: { x: 2.5, y: 0.25, z: 0.05, room: 'Analyst floor', kind: 'agent' },
  A04: { x: 8.1, y: 0.25, z: 0.05, room: 'Analyst floor', kind: 'agent' },
  A05: { x: -8.7, y: 0.25, z: 3.45, room: 'Analyst floor', kind: 'agent' },
  A06: { x: -3.1, y: 0.25, z: 3.45, room: 'Analyst floor', kind: 'agent' },
  A08: { x: 2.5, y: 0.25, z: 3.45, room: 'Analyst floor', kind: 'agent' },
  A09: { x: 8.1, y: 0.25, z: 3.45, room: 'Analyst floor', kind: 'agent' },
  A07: { x: -8.25, y: 0.25, z: 5.05, room: 'Simulation lab', kind: 'agent' },
  A10: { x: -8.35, y: 0.25, z: -4.45, room: 'PM office', kind: 'agent' },
  A11: { x: 8.35, y: 0.25, z: -4.45, room: 'CIO office', kind: 'agent' },
}

export const OFFICE_POINTS = POINTS

/** Stable display copy for compact rosters; ids remain the selection source of truth. */
export const OFFICE_ROLE_DETAILS: Record<string, { name: string; title: string; zone: string }> = {
  A00: { name: 'Chief of Staff', title: 'Chief of Staff', zone: 'Reception' },
  A01: { name: 'Universe Manager', title: 'Universe Manager', zone: 'Analyst floor' },
  A02: { name: 'Filing Reviewer', title: 'Filing Reviewer', zone: 'Analyst floor' },
  A03: { name: 'Fundamental Analyst', title: 'Fundamental Analyst', zone: 'Analyst floor' },
  A04: { name: 'Technical Analyst', title: 'Technical Analyst', zone: 'Analyst floor' },
  A05: { name: 'Entry Analyst', title: 'Entry Analyst', zone: 'Analyst floor' },
  A06: { name: 'Holdings Monitor', title: 'Holdings Monitor', zone: 'Analyst floor' },
  A07: { name: 'Simulation Analyst', title: 'Simulation Analyst', zone: 'Simulation lab' },
  A08: { name: 'Ownership & Public Filings', title: 'Ownership & Public Filings', zone: 'Analyst floor' },
  A09: { name: 'Macro Analyst', title: 'Macro Analyst', zone: 'Analyst floor' },
  A10: { name: 'Portfolio Manager', title: 'Portfolio Manager', zone: 'PM office' },
  A11: { name: 'Chief Investment Officer', title: 'Chief Investment Officer', zone: 'CIO office' },
}

export function officeRoleDetails(agent: Pick<AgentRecord, 'id' | 'name' | 'title' | 'zone'>) {
  const detail = OFFICE_ROLE_DETAILS[agent.id]
  return { name: detail?.name ?? agent.name, title: detail?.title ?? agent.title, zone: detail?.zone ?? agent.zone }
}

const SCENE_BOUNDS = { width: 27.5, depth: 18.5 }

const ANALYST_DESKS = [
  ['A01', -8.7, 0.05], ['A02', -3.1, 0.05], ['A03', 2.5, 0.05], ['A04', 8.1, 0.05],
  ['A05', -8.7, 3.45], ['A06', -3.1, 3.45], ['A08', 2.5, 3.45], ['A09', 8.1, 3.45],
] as const

const DEFAULT_ACCENTS: Record<string, string> = {
  A00: '#d5ae62', A01: '#80c6a4', A02: '#93c6d9', A03: '#c9a1d9', A04: '#e7b07c', A05: '#a3d29e', A06: '#d7c48d', A07: '#b7a1e4', A08: '#d39a9a', A09: '#8fc3b8', A10: '#d5ae62', A11: '#d5ae62',
}

function statusTone(status?: string | null) {
  if (status === 'running') return '#79d6a8'
  if (status === 'queued') return '#b7c790'
  if (status === 'waiting_for_evidence' || status === 'waiting_for_review') return '#e0b969'
  if (status === 'blocked' || status === 'failed') return '#db7f71'
  if (status === 'completed') return '#86b9cf'
  if (status === 'cancelled' || status === 'interrupted') return '#a5aaa1'
  return '#70887a'
}

type OfficeSelectionState = 'selected' | 'skipped' | 'routing_pending' | 'simulation_only' | 'unknown'

function selectionState(agent: AgentRecord, selectedRunId?: string | null): OfficeSelectionState {
  // A selected-run roster is the source of truth for these badges. If a
  // consumer retained an explicitly scoped record for a different question,
  // leave this desk unlabelled rather than carrying that old answer forward.
  if (selectedRunId && agent.selected_run_id && agent.selected_run_id !== selectedRunId) return 'unknown'
  const state = String(agent.selection_state ?? '').trim().toLowerCase()
  if (state === 'selected') return 'selected'
  if (state === 'skipped' || state === 'omitted' || state === 'not_assigned') return 'skipped'
  if (state === 'routing_pending' || state === 'pending_routing') return 'routing_pending'
  if (state === 'simulation_only' || state === 'simulation-only') return 'simulation_only'
  if (agent.selected_for_run === true) return 'selected'
  if (agent.selected_for_run === false) return 'skipped'
  return 'unknown'
}

function selectionCopy(agent: AgentRecord, selectedRunId?: string | null) {
  const state = selectionState(agent, selectedRunId)
  if (state === 'selected') return { state, label: 'Selected for question', reason: agent.selection_reason ?? 'Assigned to this question.' }
  if (state === 'skipped') return { state, label: 'Not assigned', reason: agent.selection_reason ?? 'Not assigned for this question.' }
  if (state === 'routing_pending') return { state, label: 'Routing pending', reason: agent.selection_reason ?? 'This question’s team is still being decided.' }
  if (state === 'simulation_only') return { state, label: 'Simulation only', reason: agent.selection_reason ?? 'Available from the simulation workflow.' }
  return { state, label: 'Question status unknown', reason: 'This question’s desk assignment is not available yet.' }
}

function scopedAgentOutput(agent: AgentRecord, selectedRunId?: string | null): OutputRecord | null {
  const output = agent.latest_output
  if (!output) return null
  const outputRunId = output.run_id
  // A run-scoped output must carry the same run id. Hiding an unscoped output
  // here prevents a previous question’s report from looking current.
  if (selectedRunId && outputRunId !== selectedRunId) return null
  return output
}

function scopedCompletedTask(agent: AgentRecord, selectedRunId?: string | null) {
  const task = agent.latest_completed_task
  if (!task) return null
  if (selectedRunId && task.run_id !== selectedRunId) return null
  return task
}

function Ground() {
  return <>
    <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow position={[0, -0.15, 0]}>
      <planeGeometry args={[25.5, 14.8]} />
      <meshStandardMaterial color="#1a3028" roughness={0.88} />
    </mesh>
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.12, 0]}>
      <planeGeometry args={[24.5, 13.8]} />
      <meshStandardMaterial color="#293d33" roughness={0.76} />
    </mesh>
    {Array.from({ length: 10 }).map((_, index) => <mesh key={`floor-line-${index}`} rotation={[-Math.PI / 2, 0, 0]} position={[-11.2 + index * 2.5, -0.08, 1.45]}>
      <planeGeometry args={[1.35, 0.025]} />
      <meshBasicMaterial color="#496254" transparent opacity={0.48} />
    </mesh>)}
  </>
}

function Wall({ position, args, color = '#315144', transparent = false }: { position: [number, number, number]; args: [number, number, number]; color?: string; transparent?: boolean }) {
  return <mesh position={position} castShadow receiveShadow>
    <boxGeometry args={args} />
    <meshStandardMaterial color={color} roughness={0.68} transparent={transparent} opacity={transparent ? 0.43 : 1} />
  </mesh>
}

function RoomShell({ x, z, width, depth, title, subtitle, accent = '#ba9759', office = false, labelXOffset = 0, agentId, onSelect }: { x: number; z: number; width: number; depth: number; title: string; subtitle: string; accent?: string; office?: boolean; labelXOffset?: number; agentId?: string; onSelect?: (id: string) => void }) {
  const wallColor = office ? '#4c5a4a' : '#3c5a4c'
  return <group>
    <RoundedBox args={[width, 0.16, depth]} radius={0.06} smoothness={2} position={[x, 0.02, z]} receiveShadow>
      <meshStandardMaterial color={office ? '#594b3b' : '#263e31'} roughness={0.83} />
    </RoundedBox>
    <Wall position={[x - width / 2, 1.18, z]} args={[0.18, 2.35, depth]} color={wallColor} />
    <Wall position={[x + width / 2, 1.18, z]} args={[0.18, 2.35, depth]} color={wallColor} />
    <Wall position={[x, 1.18, z + depth / 2]} args={[width, 2.35, 0.18]} color={wallColor} />
    <mesh position={[x, 1.3, z - depth / 2]}>
      <boxGeometry args={[width - 0.25, 2.1, 0.04]} />
      <meshPhysicalMaterial color="#9ed3c2" roughness={0.18} transmission={0.1} transparent opacity={0.18} />
    </mesh>
    {office && <>
      <mesh position={[x, 0.98, z + depth / 2 + 0.11]} castShadow>
        <boxGeometry args={[0.96, 1.74, 0.08]} />
        <meshStandardMaterial color="#4e3828" roughness={0.52} metalness={0.12} />
      </mesh>
      <mesh position={[x + 0.33, 0.99, z + depth / 2 + 0.17]}>
        <boxGeometry args={[0.035, 0.035, 0.03]} />
        <meshStandardMaterial color="#d5ae62" metalness={0.72} roughness={0.22} />
      </mesh>
      <Html position={[x, 1.96, z + depth / 2 + 0.2]} center distanceFactor={14} transform sprite>
        {agentId && onSelect ? <button type="button" className="scene-door-plate" onClick={(event) => { event.stopPropagation(); onSelect(agentId) }} aria-label={`Select ${agentId} ${title.includes('A10') ? 'Portfolio Manager' : 'Chief Investment Officer'}`}><span>PRIVATE OFFICE</span><strong>{title.includes('A10') ? 'PM · A10' : 'CIO · A11'}</strong></button> : <div className="scene-door-plate"><span>PRIVATE OFFICE</span><strong>{title.includes('A10') ? 'PM · A10' : 'CIO · A11'}</strong></div>}
      </Html>
    </>}
    {!office && <Html position={[x + labelXOffset, 2.35, z + depth / 2 + 0.14]} center distanceFactor={14} transform sprite>
      <div className="scene-room-label" style={{ '--scene-accent': accent } as CSSProperties}>
        <strong>{title}</strong><span>{subtitle}</span>
      </div>
    </Html>}
  </group>
}

function Desk({ position, accent, selected, onSelect, label, status, privateOffice = false, reducedMotion = false }: { position: [number, number, number]; accent: string; selected: boolean; onSelect: () => void; label: string; status?: string | null; privateOffice?: boolean; reducedMotion?: boolean }) {
  const tone = statusTone(status)
  const deskWidth = privateOffice ? 2.3 : 1.9
  const deskDepth = privateOffice ? 1.05 : 0.88
  return <group position={position} onClick={(event) => { event.stopPropagation(); onSelect() }}>
    {/* A broad transparent hit target makes the desk easy to select on a trackpad. */}
    <mesh position={[0, 0.72, 0]} onClick={(event) => { event.stopPropagation(); onSelect() }}>
      <boxGeometry args={[deskWidth + 0.42, 1.35, deskDepth + 0.5]} />
      <meshBasicMaterial transparent opacity={0} depthWrite={false} />
    </mesh>
    <RoundedBox args={[deskWidth, 0.22, deskDepth]} radius={0.08} smoothness={3} position={[0, 0.82, 0]} castShadow receiveShadow>
      <meshStandardMaterial color={privateOffice ? '#6d5235' : '#5a402c'} roughness={0.55} />
    </RoundedBox>
    <mesh position={[0, 0.4, 0]} castShadow>
      <boxGeometry args={[0.12, 0.72, 0.12]} />
      <meshStandardMaterial color="#ad8450" metalness={0.45} roughness={0.45} />
    </mesh>
    <mesh position={[0, 0.32, 0.34]} castShadow>
      <boxGeometry args={[1.3, 0.06, 0.05]} />
      <meshStandardMaterial color="#ae8550" metalness={0.25} roughness={0.55} />
    </mesh>
    <mesh position={[0, 1.1, -0.18]} castShadow>
      <boxGeometry args={[0.72, 0.44, 0.06]} />
      <meshStandardMaterial color="#16231f" emissive={status === 'running' ? '#2c7758' : '#0d1714'} emissiveIntensity={status === 'running' ? 0.65 : 0.1} roughness={0.35} />
    </mesh>
    <mesh position={[0, 0.88, 0.02]}>
      <boxGeometry args={[0.55, 0.02, 0.23]} />
      <meshStandardMaterial color={accent} emissive={accent} emissiveIntensity={selected ? 0.45 : 0.12} />
    </mesh>
    {selected && <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.07, 0]}>
      <ringGeometry args={[1.17, 1.31, 32]} />
      <meshBasicMaterial color={accent} transparent opacity={0.92} side={THREE.DoubleSide} />
    </mesh>}
    <AgentFigure accent={accent} status={status} selected={selected} position={[0, 0, privateOffice ? 0.18 : 0.12]} reducedMotion={reducedMotion} />
    <Html position={[privateOffice ? (label.startsWith('A10') ? -0.88 : 0.88) : 0, privateOffice ? 1.42 : 2.06, privateOffice ? 0.24 : 0.12]} center distanceFactor={14} transform sprite>
      <button type="button" className={`scene-agent-label${selected ? ' is-selected' : ''}`} style={{ '--scene-accent': accent } as CSSProperties} onClick={(event) => { event.stopPropagation(); onSelect() }} aria-label={`Select ${label}`}>
        <span className="scene-agent-code">{label.split(' · ')[0]}</span><strong>{label.split(' · ').slice(1).join(' · ')}</strong><i style={{ background: tone }} />
      </button>
    </Html>
  </group>
}

function AgentFigure({ accent, status, selected, position, reducedMotion }: { accent: string; status?: string | null; selected: boolean; position: [number, number, number]; reducedMotion: boolean }) {
  const ref = useRef<THREE.Group>(null)
  const animated = status === 'running' || status === 'queued'
  useFrame(({ clock }) => {
    if (!ref.current || !animated || reducedMotion) return
    ref.current.position.y = Math.sin(clock.elapsedTime * 2.5) * 0.018
  })
  return <group ref={ref} position={position}>
    <mesh position={[0, 1.04, 0]} castShadow>
      <sphereGeometry args={[0.19, 16, 12]} />
      <meshStandardMaterial color="#d0a783" roughness={0.7} />
    </mesh>
    <mesh position={[0, 0.74, 0]} castShadow>
      <capsuleGeometry args={[0.19, 0.45, 6, 12]} />
      <meshStandardMaterial color={accent} roughness={0.55} />
    </mesh>
    <mesh rotation={[0.12, 0, 0]} position={[0, 1.1, -0.12]}>
      <boxGeometry args={[0.3, 0.12, 0.03]} />
      <meshStandardMaterial color="#182721" roughness={0.3} />
    </mesh>
    {selected && <pointLight color={accent} intensity={0.42} distance={2.6} position={[0, 0.8, 0.16]} />}
  </group>
}

function Boardroom() {
  return <group>
    <RoomShell x={0} z={-4.45} width={5.6} depth={2.25} title="INVESTMENT COMMITTEE" subtitle="Evidence · disagreement · review" accent="#86b9cf" />
    <RoundedBox args={[3.5, 0.18, 1.1]} radius={0.1} smoothness={3} position={[0, 0.42, -4.45]} castShadow>
      <meshStandardMaterial color="#6c5136" roughness={0.62} />
    </RoundedBox>
    {[-1.25, -0.42, 0.42, 1.25].map((x) => <mesh key={x} position={[x, 0.76, -4.45]} castShadow>
      <cylinderGeometry args={[0.16, 0.16, 0.45, 12]} />
      <meshStandardMaterial color="#7e6950" roughness={0.75} />
    </mesh>)}
    <Html position={[0, 1.3, -4.43]} center distanceFactor={14} transform sprite>
      <div className="scene-room-chip"><span>COMMITTEE</span><strong>Boardroom</strong></div>
    </Html>
  </group>
}

function Archive() {
  return <group>
    <RoomShell x={8.2} z={5.02} width={5.0} depth={1.55} title="MEMORY ARCHIVE" subtitle="Sources · accounts · limits" accent="#b7a1e4" />
    {[7.0, 7.72, 8.44, 9.16].map((x) => <mesh key={x} position={[x, 0.75, 5.25]} castShadow>
      <boxGeometry args={[0.52, 1.16, 0.36]} />
      <meshStandardMaterial color="#5a4231" roughness={0.69} />
    </mesh>)}
    <mesh position={[9.15, 1.26, 5.25]}>
      <boxGeometry args={[0.1, 0.08, 0.38]} />
      <meshStandardMaterial color="#d5ae62" emissive="#d5ae62" emissiveIntensity={0.2} />
    </mesh>
  </group>
}

function SceneContent({ agents, selectedId, onSelect, reducedMotion }: { agents: AgentRecord[]; selectedId: string; onSelect: (id: string) => void; reducedMotion: boolean }) {
  const byId = useMemo(() => new Map(agents.map((agent) => [agent.id, agent])), [agents])
  return <>
    <color attach="background" args={['#0a1914']} />
    <fog attach="fog" args={['#0a1914', 23, 43]} />
    <ambientLight intensity={1.3} color="#d4c4a3" />
    <hemisphereLight intensity={1.4} color="#d3e9d8" groundColor="#19271f" />
    <directionalLight position={[-7, 12, 8]} intensity={2.2} color="#f4d6a0" castShadow={!reducedMotion} shadow-mapSize={[1024, 1024]} />
    <pointLight position={[0, 6, -3]} intensity={10} distance={14} color="#ba9759" />
    <pointLight position={[-8, 4, 4]} intensity={7} distance={9} color="#75bc99" />
    <Ground />
    <RoomShell x={-8.3} z={5.03} width={4.95} depth={1.58} title="SIMULATION LAB" subtitle="Assumptions · scenario replay" accent="#b7a1e4" labelXOffset={-1.18} />
    <RoomShell x={0} z={5.03} width={6.2} depth={1.58} title="CHIEF OF STAFF" subtitle="Ask the firm · route · resume" accent="#d5ae62" labelXOffset={1.6} />
    <Boardroom />
    <RoomShell x={-8.35} z={-4.43} width={4.75} depth={2.28} title="PRIVATE OFFICE / A10" subtitle="Portfolio manager · challenge queue" accent="#d5ae62" office agentId="A10" onSelect={onSelect} />
    <RoomShell x={8.35} z={-4.43} width={4.75} depth={2.28} title="PRIVATE OFFICE / A11" subtitle="CIO · allocation · risk" accent="#d5ae62" office agentId="A11" onSelect={onSelect} />
    <Archive />
    <mesh position={[0, 0.01, 1.82]} rotation={[-Math.PI / 2, 0, 0]}>
      <planeGeometry args={[22.8, 0.035]} />
      <meshBasicMaterial color="#d5ae62" transparent opacity={0.25} />
    </mesh>
    <Html position={[-11.7, 0.15, 1.7]} center distanceFactor={14} transform sprite>
      <div className="scene-section-label">ANALYST FLOOR <span>select a desk</span></div>
    </Html>
    {ANALYST_DESKS.map(([id, x, z]) => {
      const agent = byId.get(id)
      const details = agent ? officeRoleDetails(agent) : OFFICE_ROLE_DETAILS[id]
      return <Desk key={id} position={[x, 0, z]} label={`${id} · ${details?.name ?? id}`} accent={agent?.accent ?? DEFAULT_ACCENTS[id]} status={agent?.status} selected={selectedId === id} onSelect={() => onSelect(id)} reducedMotion={reducedMotion} />
    })}
    {(['A00', 'A07', 'A10', 'A11'] as const).map((id) => {
      const agent = byId.get(id)
      const point = POINTS[id]
      const details = agent ? officeRoleDetails(agent) : OFFICE_ROLE_DETAILS[id]
      return <Desk key={id} position={[point.x, 0, point.z]} label={`${id} · ${details?.name ?? id}`} accent={agent?.accent ?? DEFAULT_ACCENTS[id]} status={agent?.status} selected={selectedId === id} onSelect={() => onSelect(id)} privateOffice={id === 'A10' || id === 'A11'} reducedMotion={reducedMotion} />
    })}
    <Html position={[0, 0.9, 5.02]} center distanceFactor={14} transform sprite>
      <div className="scene-reception-sign"><span>A00</span><strong>Chief of Staff</strong></div>
    </Html>
  </>
}

function CameraReset({ resetToken, zoomDelta, reducedMotion }: { resetToken: number; zoomDelta: number; reducedMotion: boolean }) {
  const controls = useRef<any>(null)
  const { camera, size } = useThree()
  const lastReset = useRef(resetToken)
  const lastZoom = useRef(zoomDelta)
  const fitZoom = useCallback(() => {
    if (!('zoom' in camera)) return
    // R3F's orthographic frustum is measured in canvas pixels. Fitting against
    // both axes keeps the far archive and private offices in view after a
    // resize, while preserving the original isometric composition.
    const horizontal = size.width / (SCENE_BOUNDS.width * 1.08)
    const vertical = size.height / (SCENE_BOUNDS.depth * 1.12)
    camera.zoom = THREE.MathUtils.clamp(Math.min(horizontal, vertical), 10, 52)
    camera.updateProjectionMatrix()
  }, [camera, size.height, size.width])

  useEffect(() => {
    fitZoom()
  }, [fitZoom])
  useEffect(() => {
    if (lastReset.current === resetToken) return
    lastReset.current = resetToken
    camera.position.set(14, 15, 15)
    fitZoom()
    camera.lookAt(0, 0.6, 0)
    if (controls.current) { controls.current.target.set(0, 0.6, 0); controls.current.update() }
  }, [camera, fitZoom, resetToken])
  useEffect(() => {
    if (lastZoom.current === zoomDelta) return
    const direction = zoomDelta > lastZoom.current ? 1 : -1
    lastZoom.current = zoomDelta
    if ('zoom' in camera) {
      camera.zoom = THREE.MathUtils.clamp((camera as THREE.OrthographicCamera).zoom + direction * 3, 10, 52)
      camera.updateProjectionMatrix()
    }
  }, [camera, zoomDelta])
  return <OrbitControls ref={controls} makeDefault target={[0, 0.6, 0]} enableDamping={!reducedMotion} dampingFactor={0.08} minZoom={10} maxZoom={52} minPolarAngle={0.66} maxPolarAngle={1.35} minAzimuthAngle={-0.75} maxAzimuthAngle={0.75} enablePan={false} />
}

export function OfficeScene({ agents, selectedId, onSelect, resetToken, zoomDelta, reducedMotion, className = '' }: { agents: AgentRecord[]; selectedId: string; onSelect: (id: string) => void; resetToken: number; zoomDelta: number; reducedMotion: boolean; className?: string }) {
  return <div className={`office-canvas ${className}`} aria-label="Interactive 3D hedge fund office">
    <Canvas shadows={!reducedMotion} orthographic camera={{ position: [14, 15, 15], zoom: 20, near: 0.1, far: 100 }} dpr={[1, 1.7]} gl={{ antialias: true, powerPreference: 'high-performance' }}>
      <SceneContent agents={agents} selectedId={selectedId} onSelect={onSelect} reducedMotion={reducedMotion} />
      <CameraReset resetToken={resetToken} zoomDelta={zoomDelta} reducedMotion={reducedMotion} />
    </Canvas>
  </div>
}

type LogicalOfficeGroup = {
  id: string
  code: string
  name: string
  title: string
  zone: string
  memberIds: string[]
}

const LOGICAL_OFFICE_GROUPS: LogicalOfficeGroup[] = [
  { id: 'chief', code: 'A00', name: 'Chief of Staff', title: 'Routes each question and keeps the case moving', zone: 'Claim · routing', memberIds: ['A00'] },
  { id: 'researcher', code: 'A01 + A03', name: 'Researcher', title: 'Discovery and synthesis', zone: 'Evidence · research', memberIds: ['A01', 'A02', 'A03', 'A04', 'A05', 'A06', 'A07', 'A08', 'A09'] },
  { id: 'cio', code: 'A11', name: 'Chief Investment Officer', title: 'Records the current case decision', zone: 'CIO · decision', memberIds: ['A11'] },
]

const CODE_CHECK_IDS = ['A04', 'A07']

function officeStatusKey(value: unknown) {
  return String(value ?? 'idle').toLowerCase().replaceAll('-', '_').replaceAll(' ', '_') || 'idle'
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}

function groupStatus(members: AgentRecord[]) {
  const priority = ['running', 'queued', 'waiting_for_evidence', 'awaiting_input', 'waiting_for_review', 'blocked', 'failed', 'completed', 'cancelled', 'interrupted', 'idle']
  return priority.find((status) => members.some((agent) => officeStatusKey(agent.status) === status)) ?? 'idle'
}

function officeStatusCopy(status: string, hasTask = false) {
  if (status === 'running') return 'Working'
  if (status === 'queued') return 'Queued'
  if (status === 'waiting_for_evidence' || status === 'awaiting_input' || status === 'waiting_for_review') return 'Waiting'
  if (status === 'blocked') return 'Needs attention'
  if (status === 'failed') return 'Research failed'
  if (status === 'completed') return 'Recorded'
  if (status === 'cancelled' || status === 'interrupted') return 'Stopped'
  return hasTask ? 'Idle' : 'Idle · not scheduled'
}

function latestGroupMember(members: AgentRecord[]) {
  return [...members].sort((left, right) => {
    const statusRank = (value: unknown) => ['running', 'queued', 'waiting_for_evidence', 'awaiting_input', 'waiting_for_review', 'blocked', 'failed', 'completed', 'idle'].indexOf(officeStatusKey(value))
    const leftRank = statusRank(left.status)
    const rightRank = statusRank(right.status)
    if (leftRank !== rightRank) return leftRank - rightRank
    return String(right.last_update ?? '').localeCompare(String(left.last_update ?? ''))
  })[0] ?? members[0] ?? null
}

function hasRecordedCodeChecks(run?: RunDetail | null) {
  if (!run) return false
  const raw = run as unknown as Record<string, unknown>
  const context = recordValue(raw.calculation_context)
  if (Array.isArray(context?.candidates) && context.candidates.length > 0) return true
  const decision = recordValue(raw.current_decision ?? raw.canonical_decision ?? raw.current_case_decision)
  return Array.isArray(decision?.candidates) && decision.candidates.some((value) => {
    const candidate = recordValue(value)
    if (!candidate) return false
    const sizing = candidate.sizing_checks ?? candidate.sizingChecks
    return (Array.isArray(sizing) && sizing.length > 0) || Boolean(candidate.sizing_status ?? candidate.sizingStatus ?? candidate.execution_state)
  })
}

export function OfficeFallback({ agents, selectedId, onSelect, listMode = false, selectedRunId, selectedRun, onOpenOutput, onStartSimulation }: { agents: AgentRecord[]; selectedId: string; onSelect: (id: string) => void; listMode?: boolean; selectedRunId?: string | null; selectedRun?: RunDetail | null; onOpenOutput?: (output: OutputRecord) => Promise<void> | void; onStartSimulation?: () => void }) {
  const [showSpecialists, setShowSpecialists] = useState(false)
  const byId = useMemo(() => new Map(agents.map((agent) => [agent.id, agent])), [agents])
  const hasQuestionContext = Boolean(selectedRunId)

  const renderAgent = (agent: AgentRecord) => {
    const details = officeRoleDetails(agent)
    const task = agent.current_task ?? agent.task
    const taskCopy = task?.current_task ?? task?.progress_message
    const questionState = selectionCopy(agent, selectedRunId)
    const scopedQuestionContext = hasQuestionContext || agent.selected_for_run != null || agent.selection_state
    const latestOutput = scopedAgentOutput(agent, selectedRunId)
    const latestCompletedTask = scopedCompletedTask(agent, selectedRunId)
    const latestTaskCopy = latestCompletedTask?.title ?? latestCompletedTask?.current_task ?? latestCompletedTask?.mandate ?? latestCompletedTask?.progress_message
    return <article key={agent.id} className={`fallback-desk-wrap${selectedId === agent.id ? ' is-selected' : ''}`}>
      <button type="button" className={`fallback-desk${selectedId === agent.id ? ' is-selected' : ''}`} onClick={() => onSelect(agent.id)} aria-pressed={selectedId === agent.id}>
        <span className="fallback-desk-top"><span className="fallback-code">{agent.id}</span><span className="fallback-status">{officeStatusCopy(officeStatusKey(agent.status), Boolean(task))}</span></span>
        <strong>{details.name}</strong>{details.title !== details.name && <span className="fallback-title">{details.title}</span>}<span className="fallback-room">{details.zone}</span>
        {scopedQuestionContext && <span className={`fallback-question-state fallback-question-state-${questionState.state}`}><strong>{questionState.label}</strong><small>{questionState.reason}</small></span>}
        {taskCopy && <small className="fallback-task">{taskCopy}</small>}
      </button>
      {(latestOutput || latestTaskCopy) && <div className="fallback-latest-work">
        <span className="fallback-latest-label">{latestOutput ? 'LATEST SAVED REPORT' : 'LATEST COMPLETED TASK'}</span>
        <span className="fallback-latest-title">{latestOutput?.title ?? latestTaskCopy}</span>
        {latestOutput && onOpenOutput ? <button type="button" className="button button-subtle fallback-output-action" onClick={() => void onOpenOutput(latestOutput)}><span aria-hidden="true">↗</span> Open report</button> : latestOutput?.title ? <small className="fallback-latest-note">Saved report available from this desk.</small> : null}
      </div>}
      {agent.id === 'A07' && <button type="button" className="button button-primary fallback-simulation-action" onClick={() => onStartSimulation?.()} aria-label="Start simulation from the Simulation Analyst desk"><span aria-hidden="true">＋</span>Start simulation</button>}
    </article>
  }

  const renderLogicalGroup = (group: LogicalOfficeGroup) => {
    const members = group.memberIds.map((id) => byId.get(id)).filter((agent): agent is AgentRecord => Boolean(agent))
    const anchor = latestGroupMember(members)
    const status = groupStatus(members)
    const task = anchor?.current_task ?? anchor?.task
    const taskCopy = task?.current_task ?? task?.progress_message
    const selected = members.some((agent) => agent.id === selectedId)
    const latestOutput = members.map((agent) => scopedAgentOutput(agent, selectedRunId)).find(Boolean) ?? null
    const latestCompletedTask = members.map((agent) => scopedCompletedTask(agent, selectedRunId)).find(Boolean) ?? null
    const latestTaskCopy = latestCompletedTask?.title ?? latestCompletedTask?.current_task ?? latestCompletedTask?.mandate ?? latestCompletedTask?.progress_message
    const questionState = anchor ? selectionCopy(anchor, selectedRunId) : null
    return <article key={group.id} className={`fallback-desk-wrap fallback-logical-wrap${selected ? ' is-selected' : ''}`}>
      <button type="button" className={`fallback-desk fallback-logical-desk${selected ? ' is-selected' : ''}`} onClick={() => onSelect(anchor?.id ?? group.memberIds[0])} aria-pressed={selected}>
        <span className="fallback-desk-top"><span className="fallback-code">{group.code}</span><span className="fallback-status">{officeStatusCopy(status, Boolean(task))}</span></span>
        <strong>{group.name}</strong><span className="fallback-title">{group.title}</span><span className="fallback-room">{group.zone}</span>
        {questionState && hasQuestionContext && <span className={`fallback-question-state fallback-question-state-${questionState.state}`}><strong>{questionState.label}</strong><small>{members.length > 1 ? `${members.length} desks in this area · ${questionState.reason}` : questionState.reason}</small></span>}
        {taskCopy && <small className="fallback-task">{taskCopy}</small>}
        {group.id === 'researcher' && <small className="fallback-group-note">Discovery and synthesis share one readable work area.</small>}
      </button>
      {(latestOutput || latestTaskCopy) && <div className="fallback-latest-work">
        <span className="fallback-latest-label">{latestOutput ? 'LATEST SAVED REPORT' : 'LATEST COMPLETED TASK'}</span>
        <span className="fallback-latest-title">{latestOutput?.title ?? latestTaskCopy}</span>
        {latestOutput && onOpenOutput ? <button type="button" className="button button-subtle fallback-output-action" onClick={() => void onOpenOutput(latestOutput)}><span aria-hidden="true">↗</span> Open report</button> : latestOutput?.title ? <small className="fallback-latest-note">Saved report available from this area.</small> : null}
      </div>}
    </article>
  }

  const codeMembers = CODE_CHECK_IDS.map((id) => byId.get(id)).filter((agent): agent is AgentRecord => Boolean(agent))
  const codeStatus = hasRecordedCodeChecks(selectedRun) ? 'Recorded for this case' : officeStatusCopy(groupStatus(codeMembers), codeMembers.some((agent) => Boolean(agent.current_task ?? agent.task)))
  const codeTask = codeMembers.map((agent) => agent.current_task ?? agent.task).map((task) => task?.current_task ?? task?.progress_message).find(Boolean)

  return <div className="office-fallback" aria-label="Accessible office list">
    <div className="fallback-intro"><div className="fallback-intro-heading"><div><span className="eyebrow">{listMode ? 'DECISION ROLES' : '2D ACCESS VIEW'}</span><h2>Follow the case by purpose</h2></div><button type="button" className="button button-subtle fallback-specialist-toggle" onClick={() => setShowSpecialists((current) => !current)} aria-expanded={showSpecialists}>{showSpecialists ? 'Hide specialist desks' : 'Show specialist desks'}</button></div><p>{listMode ? 'Chief of Staff routes the question, Researcher handles discovery and synthesis, and the CIO records the current decision. Code checks stay visible alongside them.' : 'WebGL is unavailable in this browser. Select a decision role to open its saved work and controls.'}</p></div>
    {showSpecialists ? <div className="fallback-floor">{agents.map(renderAgent)}</div> : <div className="fallback-floor fallback-logical-floor">{LOGICAL_OFFICE_GROUPS.map(renderLogicalGroup)}<article className="fallback-desk-wrap fallback-logical-wrap fallback-code-card"><div className="fallback-desk fallback-logical-desk"><span className="fallback-desk-top"><span className="fallback-code">CODE</span><span className="fallback-status">{codeStatus}</span></span><strong>Code checks</strong><span className="fallback-title">Sizing and scenario validation</span><span className="fallback-room">Checks · code-owned</span><small className="fallback-group-note">{hasRecordedCodeChecks(selectedRun) ? 'The current case has a saved calculation context.' : selectedRun ? codeTask ?? 'No case checks recorded yet.' : 'Open a case to inspect its calculations.'}</small></div></article></div>}
  </div>
}
