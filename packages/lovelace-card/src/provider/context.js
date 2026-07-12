import { createContext } from 'preact'
import { useContext } from 'preact/hooks'

export const ProviderContext = createContext(null)

export function useProvider() {
  return useContext(ProviderContext)
}
