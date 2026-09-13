/**
 * Scene root: lighting, ground, and the layers that make up the twin.
 *
 * The clock is advanced in exactly one place — here — and every animated
 * component reads it. Vehicles and signals therefore cannot drift apart.
 */

import { useMemo } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import { Sky } from '@react-three/drei'
import * as THREE from 'three'
import type { SceneData } from './hooks/useSceneData'
import { RoadNetwork } from './components/RoadNetwork'
import { Buildings } from './components/Buildings'
import { Vehicles } from './components/Vehicles'
import { Ambulance } from './components/Ambulance'
import { TrafficSignals } from './components/TrafficSignals'
import { CameraRig, type CameraPreset } from './components/CameraRig'
import { DelayMarkers, type SignalWaitEvent } from './components/DelayMarkers'
import { SelectionMarker } from './components/SelectionMarker'
import { EmsPriorityOverlay } from './components/EmsPriorityOverlay'
import { advance, getState, setState } from './lib/store'
import { useStore } from './hooks/useStore'

/** The single clock tick for the whole scene. */
function Clock() {
  useFrame((_, delta) => advance(Math.min(delta, 0.25)))
  return null
}

function Ground({ extent }: { extent: number }) {
  return (
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.25, 0]} receiveShadow>
      <planeGeometry args={[extent, extent]} />
      <meshStandardMaterial color="#3f4a3c" roughness={1} />
    </mesh>
  )
}

export function Scene({
  data,
  preset,
  driveClock = true,
  freezeAtS = null,
}: {
  data: SceneData
  preset: CameraPreset | null
  /** Only one canvas may advance the clock; the other reads it. */
  driveClock?: boolean
  /**
   * Hold the drawn state from this simulation time on. A comparison pane passes
   * its own ambulance's recorded arrival, so it freezes there while the shared
   * clock carries on for the other pane.
   */
  freezeAtS?: number | null
}) {
  const layerFilter = useStore((s) => s.layerFilter)
  const showBuildings = useStore((s) => s.showBuildings)
  const showSignals = useStore((s) => s.showSignals)
  const showDelayMarkers = useStore((s) => s.showDelayMarkers)

  const ambulanceId = data.manifest.ambulance.vehicle_id

  /** Signals on the ambulance's route get a slightly lighter pole. */
  const highlight = useMemo(
    () => new Set(data.manifest.route_traffic_lights.map((t) => t.tls_id)),
    [data],
  )

  /**
   * Ground plane sized from the network's own footprint, not a guessed constant,
   * so the edge of the study area never floats over nothing.
   */
  const extent = useMemo(() => {
    let max = 0
    for (const lane of data.network.lanes) {
      for (const [x, , z] of lane.points) {
        max = Math.max(max, Math.abs(x), Math.abs(z))
      }
    }
    return Math.ceil((max * 2.4) / 100) * 100
  }, [data])

  return (
    <Canvas
      shadows
      dpr={[1, 1.75]}
      camera={{ fov: 55, near: 1, far: 12000, position: [420, 320, 420] }}
      gl={{ antialias: true, powerPreference: 'high-performance' }}
      onPointerMissed={() =>
        // Releasing follow too: leaving the camera locked to a vehicle the HUD
        // no longer shows as selected is the kind of hidden mode that makes a
        // 3D view feel broken.
        setState({ selectedVehicle: null, selectedSignal: null, followVehicle: null })
      }
    >
      <color attach="background" args={['#aebdd0']} />
      <fog attach="fog" args={['#aebdd0', 1400, 5200]} />
      {driveClock && <Clock />}

      <Sky sunPosition={[220, 160, -140]} turbidity={4} rayleigh={0.9} />
      <hemisphereLight args={['#cfe0f2', '#5a5f66', 2.0]} />
      <ambientLight intensity={0.55} />
      <directionalLight
        position={[300, 420, -220]}
        intensity={2.6}
        castShadow
        shadow-mapSize={[2048, 2048]}
        shadow-camera-left={-500}
        shadow-camera-right={500}
        shadow-camera-top={500}
        shadow-camera-bottom={-500}
        shadow-camera-far={1600}
      />

      <Ground extent={extent} />
      <RoadNetwork
        lanes={data.network.lanes}
        junctions={data.network.junctions}
        kerbs={data.network.kerbs}
        layerFilter={layerFilter}
      />
      {showBuildings && <Buildings buildings={data.buildings.buildings} />}
      {showSignals && (
        <TrafficSignals
          trafficLights={data.signals.traffic_lights}
          highlight={highlight}
          timeline={data.manifest.signal_timeline}
          freezeAtS={freezeAtS}
        />
      )}
      <Vehicles
        trajectories={data.trajectories}
        frameIndex={data.frameIndex}
        ambulanceId={ambulanceId}
        onPick={(vehicleIndex) => setState({ selectedVehicle: vehicleIndex })}
        freezeAtS={freezeAtS}
      />
      <Ambulance
        trajectories={data.trajectories}
        frameIndex={data.frameIndex}
        ambulanceId={ambulanceId}
        freezeAtS={freezeAtS}
      />
      {showDelayMarkers && (
        <DelayMarkers
          events={data.manifest.ambulance.signal_wait_events as SignalWaitEvent[]}
          trajectories={data.trajectories}
          frameIndex={data.frameIndex}
          ambulanceId={ambulanceId}
          freezeAtS={freezeAtS}
        />
      )}
      <EmsPriorityOverlay
        manifest={data.manifest}
        trajectories={data.trajectories}
        frameIndex={data.frameIndex}
        trafficLights={data.signals.traffic_lights}
        lanes={data.network.lanes}
        freezeAtS={freezeAtS}
      />
      <SelectionMarker
        trajectories={data.trajectories}
        frameIndex={data.frameIndex}
        ambulanceId={ambulanceId}
        freezeAtS={freezeAtS}
      />
      <CameraRig
        trajectories={data.trajectories}
        frameIndex={data.frameIndex}
        preset={preset}
        freezeAtS={freezeAtS}
      />
    </Canvas>
  )
}

export { getState }
export type { THREE }
