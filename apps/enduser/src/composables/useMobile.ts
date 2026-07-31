import { ref, onMounted, onBeforeUnmount } from "vue"

const MOBILE_BREAKPOINT = 768

export function useMobile() {
  const isMobile = ref(false)

  let mql: MediaQueryList | null = null
  const onChange = (e: MediaQueryListEvent) => {
    isMobile.value = e.matches
  }

  onMounted(() => {
    if (typeof window === "undefined" || !window.matchMedia) return
    mql = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT}px)`)
    isMobile.value = mql.matches
    mql.addEventListener("change", onChange)
  })

  onBeforeUnmount(() => {
    if (mql) mql.removeEventListener("change", onChange)
  })

  return { isMobile }
}
