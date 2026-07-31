#!/bin/bash
# K8s-specific instance management functions
# Provides NodePort allocation, ConfigMap registry, manifest rendering,
# pod operations, and container exec routing for K3s multi-instance deployment

if [[ -n "${_INSTANCE_K8S_SH_LOADED:-}" ]]; then
    return 0
fi
_INSTANCE_K8S_SH_LOADED=1

set -euo pipefail

# Source dependencies
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

# ==================== Constants ====================
readonly INSTANCE_NODEPORT_GATEWAY_BASE=30742
readonly INSTANCE_NODEPORT_DASHBOARD_BASE=30219
readonly INSTANCE_NODEPORT_MAX=32767
readonly INSTANCE_REGISTRY_CONFIGMAP="hermes-instance-registry"
readonly INSTANCE_NAMESPACE="hermes"

# ==================== Kubectl Helper ====================
_instance_kubectl=""  # cached

_get_instance_kubectl() {
    if [[ -n "$_instance_kubectl" ]]; then
        echo "$_instance_kubectl"
        return 0
    fi
    if command -v kubectl &>/dev/null; then
        _instance_kubectl="kubectl"
    elif command -v k3s &>/dev/null; then
        _instance_kubectl="k3s kubectl"
    else
        log_error "kubectl 或 k3s 未安装"
        return 1
    fi
    echo "$_instance_kubectl"
}

# ==================== NodePort Allocation ====================
allocate_instance_nodeport() {
    local base_port="${1:-$INSTANCE_NODEPORT_GATEWAY_BASE}"
    local kubectl
    kubectl="$(_get_instance_kubectl)" || return 1

    # Collect all NodePorts currently in use across the cluster
    local cluster_ports
    cluster_ports=$($kubectl get svc -A -o json 2>/dev/null | \
        jq -r '.items[].spec.ports[].nodePort // empty' 2>/dev/null | sort -n)

    # Collect ports from the registry
    local registry
    registry=$(read_registry)
    local registry_ports
    registry_ports=$(echo "$registry" | jq -r '.instances[].gateway_port // empty' 2>/dev/null | sort -n)

    local all_used
    all_used=$(echo -e "${cluster_ports}\n${registry_ports}" | sort -n | uniq | grep -v '^$')

    local port=$base_port
    while [[ $port -le $INSTANCE_NODEPORT_MAX ]]; do
        if ! echo "$all_used" | grep -q "^${port}$"; then
            echo "$port"
            return 0
        fi
        port=$((port + 1))
    done

    log_error "无法分配 NodePort: 范围 $base_port-$INSTANCE_NODEPORT_MAX 已满"
    return 1
}

allocate_instance_dashboard_nodeport() {
    local base_port="${1:-$INSTANCE_NODEPORT_DASHBOARD_BASE}"
    local kubectl
    kubectl="$(_get_instance_kubectl)" || return 1

    local cluster_ports
    cluster_ports=$($kubectl get svc -A -o json 2>/dev/null | \
        jq -r '.items[].spec.ports[].nodePort // empty' 2>/dev/null | sort -n)

    local registry
    registry=$(read_registry)
    local registry_ports
    registry_ports=$(echo "$registry" | jq -r '.instances[].dashboard_port // empty' 2>/dev/null | sort -n)

    local all_used
    all_used=$(echo -e "${cluster_ports}\n${registry_ports}" | sort -n | uniq | grep -v '^$')

    local port=$base_port
    while [[ $port -le $INSTANCE_NODEPORT_MAX ]]; do
        if ! echo "$all_used" | grep -q "^${port}$"; then
            echo "$port"
            return 0
        fi
        port=$((port + 1))
    done

    log_error "无法分配 Dashboard NodePort: 范围 $base_port-$INSTANCE_NODEPORT_MAX 已满"
    return 1
}

# ==================== ConfigMap Registry ====================
read_registry_k8s() {
    local kubectl
    kubectl="$(_get_instance_kubectl)" || { echo '{"instances":{}}'; return 0; }

    local content
    content=$($kubectl -n "$INSTANCE_NAMESPACE" get configmap "$INSTANCE_REGISTRY_CONFIGMAP" \
        -o jsonpath='{.data.registry\.json}' 2>/dev/null)

    if [[ -z "$content" ]]; then
        echo '{"instances":{}}'
    else
        echo "$content"
    fi
}

write_registry_k8s() {
    local content="$1"
    local kubectl
    kubectl="$(_get_instance_kubectl)" || return 1

    # Idempotent create-or-update via dry-run + apply
    $kubectl -n "$INSTANCE_NAMESPACE" create configmap "$INSTANCE_REGISTRY_CONFIGMAP" \
        --from-literal=registry.json="$content" \
        --dry-run=client -o yaml | $kubectl apply -f -
}

# ==================== Manifest Rendering ====================
k8s_render_and_apply_manifests() {
    local instance_id="$1"
    local provider="$2"
    local model="$3"
    local base_url="$4"
    local api_key_var_name="$5"
    local api_key_value="$6"
    local api_server_key="$7"
    local memory_limit="$8"
    local memory_request="$9"
    local cpu_limit="${10}"
    local cpu_request="${11}"
    local gateway_node_port="${12}"
    local dashboard_node_port="${13}"
    local preload_skills="${14}"
    local pvc_size="${15:-5Gi}"
    local image="${16:-hermes-profile:latest}"
    local api_mode="${17:-chat_completions}"

    local project_dir
    project_dir="$(get_project_dir)"
    local templates_dir="${project_dir}/configs/templates"

    local tmp_dir
    tmp_dir=$(mktemp -d)
    mkdir -p "${tmp_dir}/${instance_id}"

    # PVC name, ConfigMap names, Secret name
    local pvc_name="hermes-data-${instance_id}"
    local configmap_env_name="hermes-config-${instance_id}"
    local configmap_files_name="hermes-file-configs-${instance_id}"
    local secret_name="hermes-api-keys-${instance_id}"

    # Render each template
    for template in k8s-instance-pvc k8s-instance-configmap-env k8s-instance-configmap-files \
                   k8s-instance-secret k8s-instance-deployment k8s-instance-service; do
        local template_file="${templates_dir}/${template}.yaml.template"
        if [[ ! -f "$template_file" ]]; then
            log_error "模板文件不存在: $template_file"
            rm -rf "$tmp_dir"
            return 1
        fi
        sed -e "s/{{INSTANCE_ID}}/${instance_id}/g" \
            -e "s/{{PROVIDER}}/${provider}/g" \
            -e "s/{{MODEL}}/${model}/g" \
            -e "s|{{BASE_URL}}|${base_url}|g" \
            -e "s/{{API_MODE}}/${api_mode}/g" \
            -e "s/{{PRELOAD_SKILLS}}/${preload_skills}/g" \
            -e "s/{{MEMORY_LIMIT}}/${memory_limit}/g" \
            -e "s/{{MEMORY_REQUEST}}/${memory_request}/g" \
            -e "s/{{CPU_LIMIT}}/${cpu_limit}/g" \
            -e "s/{{CPU_REQUEST}}/${cpu_request}/g" \
            -e "s/{{GATEWAY_NODE_PORT}}/${gateway_node_port}/g" \
            -e "s/{{DASHBOARD_NODE_PORT}}/${dashboard_node_port}/g" \
            -e "s/{{API_SERVER_KEY}}/${api_server_key}/g" \
            -e "s/{{API_KEY_VAR_NAME}}/${api_key_var_name}/g" \
            -e "s|{{API_KEY_VALUE}}|${api_key_value}|g" \
            -e "s/{{IMAGE}}/${image}/g" \
            -e "s/{{PVC_NAME}}/${pvc_name}/g" \
            -e "s/{{PVC_SIZE}}/${pvc_size}/g" \
            -e "s/{{CONFIGMAP_ENV_NAME}}/${configmap_env_name}/g" \
            -e "s/{{CONFIGMAP_FILES_NAME}}/${configmap_files_name}/g" \
            -e "s/{{SECRET_NAME}}/${secret_name}/g" \
            "$template_file" > "${tmp_dir}/${instance_id}/${template}.yaml"
    done

    # Apply all manifests
    local kubectl
    kubectl="$(_get_instance_kubectl)" || { rm -rf "$tmp_dir"; return 1; }

    log_info "应用 K8s 资源到集群..."
    $kubectl apply -f "${tmp_dir}/${instance_id}/"

    # Cleanup temp directory
    rm -rf "$tmp_dir"
}

# ==================== Instance Deletion ====================
k8s_delete_instance_manifests() {
    local instance_id="$1"
    local kubectl
    kubectl="$(_get_instance_kubectl)" || return 1

    log_info "删除 K8s 资源: instance=$instance_id"

    # Delete all resources labeled with this instance ID
    $kubectl -n "$INSTANCE_NAMESPACE" delete deployment,svc,pvc,configmap,secret \
        -l "instance=${instance_id}" --ignore-not-found=true
}

# ==================== Pod Operations ====================
k8s_get_instance_pod_name() {
    local instance_id="$1"
    local kubectl
    kubectl="$(_get_instance_kubectl)" || return 1

    $kubectl -n "$INSTANCE_NAMESPACE" get pods \
        -l "app=hermes-gateway,instance=${instance_id}" \
        -o jsonpath='{.items[0].metadata.name}' 2>/dev/null
}

k8s_wait_for_pod_ready() {
    local instance_id="$1"
    local timeout="${2:-180}"
    local kubectl
    kubectl="$(_get_instance_kubectl)" || return 1

    log_info "等待 Pod 就绪: hermes-gateway-${instance_id} (超时 ${timeout}s)..."
    $kubectl -n "$INSTANCE_NAMESPACE" rollout status \
        deployment/hermes-gateway-${instance_id} \
        --timeout=${timeout}s
}

k8s_get_instance_status() {
    local instance_id="$1"
    local kubectl
    kubectl="$(_get_instance_kubectl)" || { echo "unknown"; return 1; }

    # Check if deployment exists
    if ! $kubectl -n "$INSTANCE_NAMESPACE" get deployment hermes-gateway-${instance_id} &>/dev/null; then
        echo "deleted"
        return 0
    fi

    # Check replica count (0 = stopped, 1 = running/scaled)
    local replicas
    replicas=$($kubectl -n "$INSTANCE_NAMESPACE" get deployment hermes-gateway-${instance_id} \
        -o jsonpath='{.spec.replicas}' 2>/dev/null || echo "0")

    if [[ "$replicas" == "0" ]]; then
        echo "stopped"
        return 0
    fi

    # Check pod readiness
    local ready_replicas
    ready_replicas=$($kubectl -n "$INSTANCE_NAMESPACE" get deployment hermes-gateway-${instance_id} \
        -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "0")

    if [[ "$ready_replicas" -ge 1 ]]; then
        echo "running"
    else
        echo "starting"
    fi
}

# ==================== Container Exec ====================
container_exec_for_instance() {
    local instance_id="$1"
    local cmd="$2"

    local kubectl
    kubectl="$(_get_instance_kubectl)" || return 1
    local pod
    pod=$(k8s_get_instance_pod_name "$instance_id")

    if [[ -z "$pod" ]]; then
        log_error "无法找到实例 Pod: $instance_id"
        return 1
    fi

    $kubectl -n "$INSTANCE_NAMESPACE" exec "$pod" -- bash -c "$cmd" 2>/dev/null
}

container_exec_for_instance_it() {
    # Interactive exec (with -it flags for terminal)
    local instance_id="$1"
    shift
    local cmd="$*"

    local kubectl
    kubectl="$(_get_instance_kubectl)" || return 1
    local pod
    pod=$(k8s_get_instance_pod_name "$instance_id")

    if [[ -z "$pod" ]]; then
        log_error "无法找到实例 Pod: $instance_id"
        return 1
    fi

    $kubectl -n "$INSTANCE_NAMESPACE" exec -it "$pod" -- bash -c "$cmd"
}

# ==================== Instance Restart ====================
restart_instance_container() {
    local instance_id="$1"
    local kubectl
    kubectl="$(_get_instance_kubectl)" || return 1

    log_info "滚动重启实例: $instance_id"
    $kubectl -n "$INSTANCE_NAMESPACE" rollout restart deployment/hermes-gateway-${instance_id}
    $kubectl -n "$INSTANCE_NAMESPACE" rollout status deployment/hermes-gateway-${instance_id} --timeout=180s
}

# ==================== Instance Health ====================
wait_for_instance_healthy() {
    local instance_id="$1"
    local max_wait="${2:-180}"

    k8s_wait_for_pod_ready "$instance_id" "$max_wait"
}

# ==================== Instance Scale ====================
scale_instance() {
    local instance_id="$1"
    local replicas="$2"
    local kubectl
    kubectl="$(_get_instance_kubectl)" || return 1

    $kubectl -n "$INSTANCE_NAMESPACE" scale deployment/hermes-gateway-${instance_id} --replicas=$replicas
}
