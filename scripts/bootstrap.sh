#!/usr/bin/env bash
# Convenience: bring up a local kind cluster, install kube-prometheus-stack,
# apply our manifests, and print port-forward instructions.
#
# Requires: kind, kubectl, helm.

set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-shortener-dev}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"

step() { echo -e "\033[1;36m==> $*\033[0m"; }

step "Checking dependencies"
for cmd in kind kubectl helm docker; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "missing dependency: $cmd"; exit 1
  fi
done

if ! kind get clusters | grep -qx "$CLUSTER_NAME"; then
  step "Creating kind cluster '$CLUSTER_NAME'"
  kind create cluster --name "$CLUSTER_NAME"
else
  step "Reusing existing kind cluster '$CLUSTER_NAME'"
fi

kubectl cluster-info --context "kind-$CLUSTER_NAME" >/dev/null

step "Installing kube-prometheus-stack"
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
helm repo update >/dev/null
kubectl create ns monitoring --dry-run=client -o yaml | kubectl apply -f -
helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  -n monitoring -f "$HERE/k8s/monitoring/prometheus-values.yaml" --wait

step "Applying app manifests (dev overlay)"
kubectl apply -k "$HERE/k8s/overlays/dev"

step "Applying monitoring config"
kubectl apply -f "$HERE/k8s/monitoring/servicemonitors.yaml"
kubectl apply -f "$HERE/k8s/monitoring/alerts.yaml"
kubectl apply -f "$HERE/k8s/monitoring/grafana-dashboard.yaml"
kubectl apply -f "$HERE/k8s/monitoring/alertmanager-config.yaml" || \
  echo "  (alertmanager-config.yaml requires v1alpha1 CRDs; skipped if not installed)"

step "Waiting for the app to become Ready"
kubectl -n shortener rollout status deploy/api --timeout=180s
kubectl -n shortener rollout status deploy/worker --timeout=180s

cat <<EOF

✓ shortener is up.

  Port-forwards:
    kubectl -n shortener        port-forward svc/api      8000:8000   # the app
    kubectl -n monitoring       port-forward svc/kube-prometheus-stack-grafana 3000:80   # Grafana (admin/shortener-admin)
    kubectl -n monitoring       port-forward svc/kube-prometheus-stack-prometheus 9090:9090

  Smoke test:
    curl -s -X POST localhost:8000/shorten -H 'Content-Type: application/json' \\
        -d '{"url":"https://example.com"}'

  Inject a fault:
    curl -X POST 'localhost:8000/_chaos/leak?mb=80'

EOF
