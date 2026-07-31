<script setup lang="ts">
import pkg from "../../../package.json";
const version = pkg.version;
</script>

<template>
  <div class="version-footer">v{{ version }}</div>
</template>
