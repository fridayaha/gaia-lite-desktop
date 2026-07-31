/// <reference types="vite/client" />

declare module "*.vue" {
  import type { DefineComponent } from "vue";
  const component: DefineComponent<{}, {}, any>;
  export default component;
}

// streaming-markdown (smd) —— 无官方类型，手写声明
declare module "streaming-markdown" {
  export function default_renderer(el: HTMLElement): any;
  export function parser(renderer: any): any;
  export function parser_write(parser: any, text: string): void;
  export function parser_end(parser: any): void;
}
