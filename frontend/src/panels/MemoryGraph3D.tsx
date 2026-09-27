import { Html, OrbitControls, Billboard } from '@react-three/drei'
import { Canvas, useFrame, useThree, type ThreeEvent } from '@react-three/fiber'
import { Component, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ComponentRef, type ReactNode } from 'react'
import * as THREE from 'three'
import { isWebGLAvailable } from '../office/webgl'
import { layoutMemoryGraph3D, linkedMemoryNodes, memoryKind, type PositionedMemoryNode3D, type SharedMemoryGraph } from './memoryGraphModel'

export type MemoryCameraAction = { type: 'fit' | 'focus' | 'zoom_in' | 'zoom_out'; nonce: number }

export interface MemoryGraph3DProps {
  graph: SharedMemoryGraph
  selectedId: string | null
  onSelect: (id: string) => void
  cameraAction: MemoryCameraAction
  /** Limit what is drawn without moving nodes in the underlying layout. */
  visibleIds?: Set<string>
  onUnavailable?: () => void
}

class SceneBoundary extends Component<{ children: ReactNode; onUnavailable?: () => void }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  componentDidCatch() { this.props.onUnavailable?.() }
  render() { return this.state.failed ? null : this.props.children }
}

function useReducedMotion() {
  const [reduced, setReduced] = useState(() => typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches)
  useEffect(() => {
    const query = window.matchMedia('(prefers-reduced-motion: reduce)')
    const change = () => setReduced(query.matches)
    query.addEventListener('change', change)
    return () => query.removeEventListener('change', change)
  }, [])
  return reduced
}

function boundsOf(nodes: PositionedMemoryNode3D[]) {
  const box = new THREE.Box3()
  for (const node of nodes) {
    box.expandByPoint(new THREE.Vector3(node.x - node.radius, node.y - node.radius, node.z - node.radius))
    box.expandByPoint(new THREE.Vector3(node.x + node.radius, node.y + node.radius, node.z + node.radius))
  }
  return { center: nodes.length ? box.getCenter(new THREE.Vector3()) : new THREE.Vector3(), radius: nodes.length ? Math.max(30, box.getSize(new THREE.Vector3()).length() / 2) : 100 }
}

/** The camera rests when untouched; demand rendering only runs during interaction. */
function Camera({ nodes, selectedId, action, reducedMotion }: { nodes: PositionedMemoryNode3D[]; selectedId: string | null; action: MemoryCameraAction; reducedMotion: boolean }) {
  const controls = useRef<ComponentRef<typeof OrbitControls>>(null)
  const { camera, size, invalidate, gl } = useThree()
  const bounds = useMemo(() => boundsOf(nodes), [nodes])
  const transition = useRef<{ position: THREE.Vector3; target: THREE.Vector3 } | null>(null)
  const lastAction = useRef<number | null>(null)
  const perspective = camera as THREE.PerspectiveCamera
  const verticalFov = THREE.MathUtils.degToRad(perspective.fov)
  const horizontalFov = 2 * Math.atan(Math.tan(verticalFov / 2) * size.width / Math.max(1, size.height))
  const fitScale = 1.12 / Math.sin(Math.min(verticalFov, horizontalFov) / 2)
  const fitDistance = bounds.radius * fitScale
  const minDistance = Math.max(14, bounds.radius * .06)

  const move = useCallback((type: MemoryCameraAction['type']) => {
    const orbit = controls.current
    if (!orbit) return
    const target = orbit.target.clone()
    const direction = camera.position.clone().sub(target).normalize()
    let distance = camera.position.distanceTo(target)
    if (type === 'fit') {
      target.copy(bounds.center)
      direction.set(.62, .34, 1.5).normalize()
      distance = fitDistance
    } else if (type === 'focus') {
      const node = nodes.find((item) => item.id === selectedId)
      if (!node) return
      if (node.kind === 'company') {
        // A company shortcut frames its whole visible notebook, including on
        // narrow screens where a fixed distance would crop connected notes.
        const company = boundsOf(nodes.filter((item) => item.id === node.id || Boolean(node.ticker && item.ticker === node.ticker)))
        target.copy(company.center)
        distance = company.radius * fitScale
      } else {
        target.set(node.x, node.y, node.z)
        distance = Math.max(100, Math.min(fitDistance * .48, Math.max(220, node.radius * 28)))
      }
    } else {
      distance = THREE.MathUtils.clamp(distance * (type === 'zoom_in' ? .76 : 1.32), minDistance, fitDistance * 6)
    }
    const position = target.clone().addScaledVector(direction, distance)
    if (reducedMotion) {
      transition.current = null
      camera.position.copy(position)
      orbit.target.copy(target)
      orbit.update()
    } else transition.current = { position, target }
    invalidate()
  }, [bounds, camera, fitDistance, fitScale, invalidate, minDistance, nodes, reducedMotion, selectedId])

  // Selection changes never reset the camera. A new graph or visible subset fits once.
  useEffect(() => {
    const orbit = controls.current
    if (!orbit) return
    transition.current = null
    orbit.target.copy(bounds.center)
    camera.position.copy(bounds.center).addScaledVector(new THREE.Vector3(.62, .34, 1.5).normalize(), fitDistance)
    orbit.update()
    invalidate()
  }, [bounds, camera, fitDistance, invalidate])
  useEffect(() => {
    if (lastAction.current === action.nonce) return
    lastAction.current = action.nonce
    move(action.type)
  }, [action, move])
  useEffect(() => {
    const canvas = gl.domElement
    canvas.tabIndex = 0
    canvas.setAttribute('aria-label', '3D memory map. Drag to rotate, right-drag to move, and scroll to zoom. Arrow keys rotate, plus and minus zoom, and zero resets the view. Use the note list to browse every note.')
    const keyDown = (event: KeyboardEvent) => {
      const orbit = controls.current
      if (!orbit) return
      if (['+', '=', '-', '0'].includes(event.key)) {
        event.preventDefault()
        move(event.key === '0' ? 'fit' : event.key === '-' ? 'zoom_out' : 'zoom_in')
      } else if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) {
        event.preventDefault()
        transition.current = null
        if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') orbit.setAzimuthalAngle(orbit.getAzimuthalAngle() + (event.key === 'ArrowLeft' ? .14 : -.14))
        else orbit.setPolarAngle(THREE.MathUtils.clamp(orbit.getPolarAngle() + (event.key === 'ArrowUp' ? -.14 : .14), .08, Math.PI - .08))
        invalidate()
      }
    }
    canvas.addEventListener('keydown', keyDown)
    return () => canvas.removeEventListener('keydown', keyDown)
  }, [gl, invalidate, move])
  useFrame((_, delta) => {
    const goal = transition.current, orbit = controls.current
    if (!goal || !orbit) return
    const amount = 1 - Math.exp(-Math.min(delta, .08) * 9)
    camera.position.lerp(goal.position, amount)
    orbit.target.lerp(goal.target, amount)
    if (camera.position.distanceToSquared(goal.position) < .01 && orbit.target.distanceToSquared(goal.target) < .01) {
      camera.position.copy(goal.position)
      orbit.target.copy(goal.target)
      transition.current = null
    }
    orbit.update()
    invalidate()
  })
  return <OrbitControls ref={controls} makeDefault enableDamping={!reducedMotion} dampingFactor={.13} rotateSpeed={.65} zoomSpeed={.8} panSpeed={.8}
    minDistance={minDistance} maxDistance={fitDistance * 6} minPolarAngle={.08} maxPolarAngle={Math.PI - .08} screenSpacePanning
    touches={{ ONE: THREE.TOUCH.ROTATE, TWO: THREE.TOUCH.DOLLY_PAN }} onStart={() => { transition.current = null }} />
}

function Links({ graph, nodes, selectedId }: { graph: SharedMemoryGraph; nodes: PositionedMemoryNode3D[]; selectedId: string | null }) {
  const geometry = useMemo(() => {
    const positions = new Map(nodes.map((node) => [node.id, node]))
    const vertices: number[] = [], colors: number[] = []
    for (const edge of graph.edges) {
      const source = positions.get(edge.source), target = positions.get(edge.target)
      if (!source || !target || source === target) continue
      vertices.push(source.x, source.y, source.z, target.x, target.y, target.z)
      const active = edge.source === selectedId || edge.target === selectedId
      const color = new THREE.Color(active ? '#8ead9f' : selectedId ? '#253b37' : '#47635a')
      colors.push(color.r, color.g, color.b, color.r, color.g, color.b)
    }
    const result = new THREE.BufferGeometry()
    result.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3))
    result.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3))
    return result
  }, [graph.edges, nodes, selectedId])
  useEffect(() => () => geometry.dispose(), [geometry])
  return <lineSegments geometry={geometry}><lineBasicMaterial vertexColors transparent opacity={.72} depthWrite={false} /></lineSegments>
}

/** An expanded hit target must never steal a click from a visible sphere. */
export function pickMemoryNode(nodes: PositionedMemoryNode3D[], intersections: Array<Pick<THREE.Intersection, 'object' | 'instanceId'>>, dots: THREE.Object3D | null, targets: THREE.Object3D | null, pointer: { x: number; y: number }, camera: THREE.Camera, size: { width: number; height: number }) {
  const visibleHit = intersections.find((hit) => hit.object === dots && hit.instanceId != null && nodes[hit.instanceId])
  if (visibleHit?.instanceId != null) return nodes[visibleHit.instanceId]
  let closest: PositionedMemoryNode3D | undefined, closestDistance = Infinity
  const visited = new Set<number>()
  const projected = new THREE.Vector3()
  for (const hit of intersections) {
    if (hit.object !== targets || hit.instanceId == null || visited.has(hit.instanceId)) continue
    visited.add(hit.instanceId)
    const node = nodes[hit.instanceId]
    if (!node) continue
    projected.set(node.x, node.y, node.z).project(camera)
    const distance = ((projected.x - pointer.x) * size.width) ** 2 + ((projected.y - pointer.y) * size.height) ** 2
    if (distance < closestDistance) { closest = node; closestDistance = distance }
  }
  return closest
}

function Nodes({ nodes, graph, selectedId, hoverId, onHover, onSelect }: { nodes: PositionedMemoryNode3D[]; graph: SharedMemoryGraph; selectedId: string | null; hoverId: string | null; onHover: (id: string | null) => void; onSelect: (id: string) => void }) {
  const dots = useRef<THREE.InstancedMesh>(null)
  const targets = useRef<THREE.InstancedMesh>(null)
  const { camera, size, invalidate } = useThree()
  const connected = useMemo(() => linkedMemoryNodes(selectedId, graph.edges), [selectedId, graph.edges])
  const scratch = useMemo(() => new THREE.Object3D(), [])
  useLayoutEffect(() => {
    if (!dots.current) return
    for (const [index, node] of nodes.entries()) {
      scratch.position.set(node.x, node.y, node.z)
      scratch.scale.setScalar(node.radius * (node.id === selectedId ? 1.2 : node.id === hoverId ? 1.12 : 1))
      scratch.updateMatrix()
      dots.current.setMatrixAt(index, scratch.matrix)
      const color = new THREE.Color(memoryKind(node.kind).color)
      if (selectedId && !connected.has(node.id)) color.lerp(new THREE.Color('#20352e'), .62)
      dots.current.setColorAt(index, color)
    }
    dots.current.instanceMatrix.needsUpdate = true
    if (dots.current.instanceColor) dots.current.instanceColor.needsUpdate = true
    dots.current.computeBoundingSphere()
    invalidate()
  }, [connected, hoverId, invalidate, nodes, scratch, selectedId])
  // Picking targets stay about 22px wide at any distance, independent of labels.
  useFrame(() => {
    if (!targets.current) return
    const perspective = camera as THREE.PerspectiveCamera
    for (const [index, node] of nodes.entries()) {
      scratch.position.set(node.x, node.y, node.z)
      const distance = camera.position.distanceTo(scratch.position)
      const pixelScale = 2 * distance * Math.tan(THREE.MathUtils.degToRad(perspective.fov) / 2) / Math.max(1, size.height)
      scratch.scale.setScalar(Math.max(node.radius * 1.45, Math.min(pixelScale * 11, node.radius * 5)))
      scratch.updateMatrix()
      targets.current.setMatrixAt(index, scratch.matrix)
    }
    targets.current.instanceMatrix.needsUpdate = true
    targets.current.computeBoundingSphere()
  })
  const hit = (event: ThreeEvent<PointerEvent | MouseEvent>) => pickMemoryNode(nodes, event.intersections, dots.current, targets.current, event.pointer, camera, size)
  const pointerMove = (event: ThreeEvent<PointerEvent>) => { const node = hit(event); if (node) { event.stopPropagation(); onHover(node.id) } }
  const click = (event: ThreeEvent<MouseEvent>) => { const node = hit(event); if (node && event.delta <= 5) { event.stopPropagation(); onSelect(node.id) } }
  return <>
    <instancedMesh ref={dots} args={[undefined, undefined, nodes.length]} frustumCulled={false} onPointerMove={pointerMove} onPointerOut={() => onHover(null)} onClick={click}>
      <sphereGeometry args={[1, 16, 12]} /><meshStandardMaterial roughness={.38} metalness={.08} />
    </instancedMesh>
    <instancedMesh ref={targets} args={[undefined, undefined, nodes.length]} frustumCulled={false}
      onPointerMove={pointerMove} onPointerOut={() => onHover(null)} onClick={click}>
      <sphereGeometry args={[1, 10, 8]} /><meshBasicMaterial transparent opacity={0} depthWrite={false} />
    </instancedMesh>
  </>
}

function Scene({ graph, selectedId, onSelect, cameraAction, visibleIds, onUnavailable }: MemoryGraph3DProps) {
  const allNodes = useMemo(() => layoutMemoryGraph3D(graph.nodes, graph.edges), [graph.nodes, graph.edges])
  const nodes = useMemo(() => visibleIds ? allNodes.filter((node) => visibleIds.has(node.id)) : allNodes, [allNodes, visibleIds])
  const [hoverId, setHoverId] = useState<string | null>(null)
  const { gl, invalidate } = useThree()
  const reducedMotion = useReducedMotion()
  const selected = nodes.find((node) => node.id === selectedId)
  const degrees = useMemo(() => {
    const result = new Map<string, Set<string>>()
    for (const edge of graph.edges) {
      if (!result.has(edge.source)) result.set(edge.source, new Set())
      if (!result.has(edge.target)) result.set(edge.target, new Set())
      result.get(edge.source)!.add(edge.target)
      result.get(edge.target)!.add(edge.source)
    }
    return result
  }, [graph.edges])
  const labels = useMemo(() => {
    const companyIds = new Set(nodes.filter((node) => node.kind === 'company').slice(0, 24).map((node) => node.id))
    return nodes.filter((node) => companyIds.has(node.id) || node.id === selectedId || node.id === hoverId)
  }, [nodes, selectedId, hoverId])
  useEffect(() => {
    const canvas = gl.domElement
    const lost = (event: Event) => { event.preventDefault(); onUnavailable?.() }
    canvas.addEventListener('webglcontextlost', lost)
    return () => canvas.removeEventListener('webglcontextlost', lost)
  }, [gl, onUnavailable])
  useEffect(() => { gl.domElement.style.cursor = hoverId ? 'pointer' : 'grab'; invalidate() }, [gl, hoverId, invalidate])
  return <>
    <color attach="background" args={['#101d1a']} />
    <ambientLight intensity={1.6} />
    <directionalLight position={[300, 500, 600]} intensity={2.1} />
    <directionalLight position={[-500, -100, -300]} intensity={.65} color="#a8cec0" />
    <Camera nodes={nodes} selectedId={selectedId} action={cameraAction} reducedMotion={reducedMotion} />
    <Links graph={graph} nodes={nodes} selectedId={selectedId} />
    <Nodes nodes={nodes} graph={graph} selectedId={selectedId} hoverId={hoverId} onHover={setHoverId} onSelect={onSelect} />
    {selected && <Billboard position={[selected.x, selected.y, selected.z]}>
      <mesh><torusGeometry args={[selected.radius * 1.7, .8, 8, 48]} /><meshBasicMaterial color="#f9e8b1" transparent opacity={.95} depthTest={false} /></mesh>
    </Billboard>}
    {labels.map((node) => <Html key={node.id} position={[node.x, node.y, node.z]} center zIndexRange={[30, 10]} style={{ pointerEvents: 'none' }}>
      <button type="button" className={`memory-3d-label${node.kind === 'company' ? ' is-company' : ''}${node.id === selectedId ? ' is-selected' : ''}`} style={{ pointerEvents: 'auto', transform: 'translateY(28px)' }}
        title={node.title} aria-label={`${memoryKind(node.kind).label}: ${node.title}`} aria-pressed={node.id === selectedId} onClick={() => onSelect(node.id)}>
        <strong>{node.kind === 'company' ? node.ticker || node.title : node.title.length > 74 ? `${node.title.slice(0, 71)}…` : node.title}</strong>
        <span>{node.kind === 'company' ? `${degrees.get(node.id)?.size ?? 0} connected notes` : memoryKind(node.kind).label}</span>
      </button>
    </Html>)}
  </>
}

export default function MemoryGraph3D(props: MemoryGraph3DProps) {
  const [available] = useState(() => isWebGLAvailable())
  useEffect(() => { if (!available) props.onUnavailable?.() }, [available, props.onUnavailable])
  return <div className="memory-graph-3d">
    {available && <SceneBoundary onUnavailable={props.onUnavailable}>
      <Canvas frameloop="demand" dpr={[1, 1.6]} camera={{ position: [200, 160, 800], fov: 46, near: .1, far: 50000 }}
        gl={{ antialias: true, alpha: false, powerPreference: 'low-power' }} fallback="Use the note list for a text version of this interactive memory map.">
        <Scene {...props} />
      </Canvas>
    </SceneBoundary>}
  </div>
}
