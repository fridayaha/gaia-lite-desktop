import { createRouter, createWebHistory } from 'vue-router'
import ItemsList from '../views/ItemsList.vue'
import ItemDetail from '../views/ItemDetail.vue'
import RuntimeDebug from '../views/RuntimeDebug.vue'

const routes = [
  { path: '/', redirect: '/items' },
  { path: '/items', name: 'ItemsList', component: ItemsList },
  { path: '/items/:id', name: 'ItemDetail', component: ItemDetail, props: true },
  { path: '/runtime', name: 'RuntimeDebug', component: RuntimeDebug },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
