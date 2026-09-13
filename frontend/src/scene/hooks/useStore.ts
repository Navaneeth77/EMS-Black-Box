/** React binding for the external simulation store. */

import { useSyncExternalStore } from 'react'
import { getState, subscribe, type SimState } from '../lib/store'

export function useStore<T>(select: (state: SimState) => T): T {
  return useSyncExternalStore(
    subscribe,
    () => select(getState()),
    () => select(getState()),
  )
}
