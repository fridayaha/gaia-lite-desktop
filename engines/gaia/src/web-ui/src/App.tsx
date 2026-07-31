import { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { AppLayout } from './components/Layout';
import { RequireAuth } from './components/RequireAuth';
import { OntologyWorkspace } from './pages/OntologyWorkspace';
import { DataSourcesPage } from './pages/DataSourcesPage';
import { DatasetsPage } from './pages/DatasetsPage';
import { DataSourceDetail } from './pages/DataSourceDetail';
import { SyncTaskDetail } from './pages/SyncTaskDetail';
import { DatasetDetail } from './pages/DatasetDetail';
import { ActionsOverview } from './pages/ActionsOverview';
import { OperationsDashboard } from './pages/OperationsDashboard';
import { PipelinesPage } from './pages/PipelinesPage';
import { PipelineBuilderPage } from './pages/PipelineBuilderPage';
import { CheckAccessPage } from './pages/CheckAccessPage';
import { IdentityManagementPage } from './pages/IdentityManagementPage';
import { AccessRequestsPage } from './pages/AccessRequestsPage';
import { AuditLogsPage } from './pages/AuditLogsPage';
import { MarkingsManagementPage } from './pages/MarkingsManagementPage';
import { ErrorBoundary } from './components/ErrorBoundary';
import { PermissionedRoute, ForbiddenPage } from './components/permission';

// B6: 图探索页懒加载——lite 版不注册其路由，整个 chunk（含 cytoscape 扩展 + maplibre
// ~700KB）被 Vite tree-shake 掉。full 版 __EDITION__ !== 'lite' 才注册路由并触发加载。
const GraphExplorePage = __EDITION__ !== 'lite'
  ? lazy(() => import('./pages/GraphExplorePage').then((m) => ({ default: m.GraphExplorePage })))
  : null;
const IS_LITE = __EDITION__ === 'lite';

export default function App() {
  return (
    <ErrorBoundary>
      <BrowserRouter>
        <Suspense fallback={null}>
        <Routes>
          <Route
            element={
              <RequireAuth>
                <AppLayout />
              </RequireAuth>
            }
          >
            <Route
              path="/"
              element={
                <ErrorBoundary>
                  <OntologyWorkspace />
                </ErrorBoundary>
              }
            />
            <Route
              path="/data/sources"
              element={
                <ErrorBoundary>
                  <DataSourcesPage />
                </ErrorBoundary>
              }
            />
            <Route
              path="/data/datasets"
              element={
                <ErrorBoundary>
                  <DatasetsPage />
                </ErrorBoundary>
              }
            />
            <Route
              path="/data/sources/:name"
              element={
                <ErrorBoundary>
                  <DataSourceDetail />
                </ErrorBoundary>
              }
            />
            <Route
              path="/data/syncs/:name"
              element={
                <ErrorBoundary>
                  <SyncTaskDetail />
                </ErrorBoundary>
              }
            />
            <Route
              path="/data/datasets/:name"
              element={
                <ErrorBoundary>
                  <DatasetDetail />
                </ErrorBoundary>
              }
            />
            <Route
              path="/actions"
              element={
                <ErrorBoundary>
                  <ActionsOverview />
                </ErrorBoundary>
              }
            />
            <Route
              path="/ops"
              element={
                <ErrorBoundary>
                  <OperationsDashboard />
                </ErrorBoundary>
              }
            />
            {/* B6: 图探索路由 lite 版不注册（GraphExplorePage chunk 不打包，tree-shake
                maplibre + cytoscape 扩展）。full 版才注册。 */}
            {!IS_LITE && GraphExplorePage && (
              <>
                <Route
                  path="/explore"
                  element={
                    <ErrorBoundary>
                      <GraphExplorePage />
                    </ErrorBoundary>
                  }
                />
                <Route
                  path="/explore/:ontology"
                  element={
                    <ErrorBoundary>
                      <GraphExplorePage />
                    </ErrorBoundary>
                  }
                />
              </>
            )}
            {/* ADR-016 permission governance (Phase 5) */}
            {/* ── Pipeline Builder ── */}
            <Route
              path="/pipelines"
              element={
                <ErrorBoundary>
                  <PipelinesPage />
                </ErrorBoundary>
              }
            />
            <Route
              path="/pipelines/new"
              element={
                <ErrorBoundary>
                  <PipelineBuilderPage />
                </ErrorBoundary>
              }
            />
            <Route
              path="/pipelines/:apiName"
              element={
                <ErrorBoundary>
                  <PipelineBuilderPage />
                </ErrorBoundary>
              }
            />
            <Route
              path="/authz/identity"
              element={
                <ErrorBoundary>
                  <IdentityManagementPage />
                </ErrorBoundary>
              }
            />
            <Route
              path="/authz/check"
              element={
                <ErrorBoundary>
                  <CheckAccessPage />
                </ErrorBoundary>
              }
            />
            <Route
              path="/authz/requests"
              element={
                <ErrorBoundary>
                  <AccessRequestsPage />
                </ErrorBoundary>
              }
            />
            <Route
              path="/authz/markings"
              element={
                <ErrorBoundary>
                  <PermissionedRoute
                    resourceType="MARKING"
                    resourceId="*"
                    action="marking:manage"
                    fallback={<ForbiddenPage action="marking:manage" resourceType="MARKING" />}
                  >
                    <MarkingsManagementPage />
                  </PermissionedRoute>
                </ErrorBoundary>
              }
            />
            <Route
              path="/authz/audit"
              element={
                <ErrorBoundary>
                  <PermissionedRoute
                    resourceType="AUDIT"
                    resourceId="*"
                    action="audit:read"
                    fallback={<ForbiddenPage action="audit:read" resourceType="AUDIT" />}
                  >
                    <AuditLogsPage />
                  </PermissionedRoute>
                </ErrorBoundary>
              }
            />
          </Route>
        </Routes>
        </Suspense>
      </BrowserRouter>
    </ErrorBoundary>
  );
}
