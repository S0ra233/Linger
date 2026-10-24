<script setup lang="ts">
defineProps<{ name: string }>()
const paths: Record<string, string> = {
  overview: 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
  contacts: 'M20 21v-2a6 6 0 0 0-12 0v2 M14 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8 M3 9v6 M0 12h6',
  materials: 'M4 4h6l2 2h8v14H4z M8 10h8 M8 14h6',
  models: 'M7 2v4 M17 2v4 M7 18v4 M17 18v4 M2 7h4 M18 7h4 M2 17h4 M18 17h4 M6 6h12v12H6z M10 10h4v4h-4z',
  runtime: 'M3 12h4l3-8 4 16 3-8h4',
  integrations: 'M8 3v5 M16 3v5 M6 8h12v3a6 6 0 0 1-6 6v4 M6 8v3a6 6 0 0 0 6 6',
  settings: 'M4 6h16 M4 12h16 M4 18h16 M8 3v6 M16 9v6 M10 15v6',
  appearance: 'M12 3a9 9 0 1 0 0 18h1a2 2 0 0 0 0-4h-.5a1.5 1.5 0 0 1 0-3H16a5 5 0 0 0 5-5c0-3.3-4-6-9-6z M7 8h.01 M11 6h.01 M16 8h.01 M6 12h.01',
  close: 'm6 6 12 12 M6 18 18 6',
  check: 'm5 12 4 4L19 6',
  chevron: 'm8 10 4 4 4-4',
  link: 'M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-2 2 M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l2-2',
  document: 'M5 3h9l5 5v13H5z M14 3v5h5 M9 12h6 M9 16h6',
  user: 'M20 21v-2a8 8 0 0 0-16 0v2 M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8',
  lock: 'M7 10V7a5 5 0 0 1 10 0v3 M5 10h14v11H5z M12 14v3',
  'arrow-right': 'M4 12h16 m-6-6 6 6-6 6',
}
</script>
<template><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="paths[name] || paths.materials" /></svg></template>
