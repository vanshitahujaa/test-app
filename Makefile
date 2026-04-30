.PHONY: help up down logs ps build test smoke load \
        k8s-up k8s-down k8s-status \
        monitoring-up alerts-apply \
        inject-leak inject-cpu inject-crash recover

PROJECT      := shortener
COMPOSE      := docker compose
KUBECTL      := kubectl
HELM         := helm
NS           := shortener
MON_RELEASE  := kube-prometheus-stack
MON_NS       := monitoring
API_URL      ?= http://localhost:8000

help:                  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | sort | \
	  awk -F ':.*?## ' '{ printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2 }'

# ─── docker-compose: fast local loop ────────────────────────────────

up:                    ## Start the stack on docker-compose
	$(COMPOSE) up --build -d

down:                  ## Stop and remove containers
	$(COMPOSE) down -v

logs:                  ## Tail all service logs
	$(COMPOSE) logs -f --tail=50

ps:                    ## List running containers
	$(COMPOSE) ps

build:                 ## (Re)build the images
	$(COMPOSE) build

# ─── smoke tests ────────────────────────────────────────────────────

smoke:                 ## Create a link, follow it, check stats
	@set -e ;\
	resp=$$(curl -fsS -X POST $(API_URL)/shorten -H 'Content-Type: application/json' -d '{"url":"https://example.com"}') ;\
	code=$$(printf '%s' "$$resp" | python3 -c 'import json,sys;print(json.load(sys.stdin)["code"])') ;\
	echo "shortened: $$code -> $$resp" ;\
	curl -fsSI $(API_URL)/$$code | head -1 ;\
	curl -fsS $(API_URL)/stats/$$code

load:                  ## Light load: 200 reqs over ~20s
	@for i in $$(seq 1 200); do \
	  curl -s -o /dev/null -X POST $(API_URL)/shorten -H 'Content-Type: application/json' \
	    -d "{\"url\":\"https://example.com/page-$$i\"}" ; \
	  sleep 0.1 ; \
	done

# ─── chaos triggers ─────────────────────────────────────────────────

inject-leak:           ## Leak ~50MB into the api process
	curl -fsS -X POST '$(API_URL)/_chaos/leak?mb=50' | python3 -m json.tool

inject-cpu:            ## Burn 2 cores for 60s on the api process
	curl -fsS -X POST '$(API_URL)/_chaos/cpu?threads=2&seconds=60' | python3 -m json.tool

inject-crash:          ## Force the api to crash (will be restarted)
	-curl -fsS -X POST '$(API_URL)/_chaos/crash'

recover:               ## Free leaked memory + stop CPU burners
	curl -fsS -X POST '$(API_URL)/_chaos/recover' | python3 -m json.tool

# ─── kubernetes ─────────────────────────────────────────────────────

k8s-up:                ## Apply the dev overlay to current kube context
	$(KUBECTL) apply -k k8s/overlays/dev

k8s-down:              ## Tear down everything
	$(KUBECTL) delete -k k8s/overlays/dev || true
	$(KUBECTL) delete ns $(NS) --ignore-not-found

k8s-status:            ## Show pods, services, and recent events in the namespace
	$(KUBECTL) -n $(NS) get pods,svc
	@echo "---"
	$(KUBECTL) -n $(NS) get events --sort-by=.metadata.creationTimestamp | tail -15

monitoring-up:         ## Install kube-prometheus-stack + ServiceMonitors + alerts + dashboard
	$(HELM) repo add prometheus-community https://prometheus-community.github.io/helm-charts || true
	$(HELM) repo update
	$(KUBECTL) create ns $(MON_NS) --dry-run=client -o yaml | $(KUBECTL) apply -f -
	$(HELM) upgrade --install $(MON_RELEASE) prometheus-community/kube-prometheus-stack \
	  -n $(MON_NS) -f k8s/monitoring/prometheus-values.yaml --wait
	$(KUBECTL) apply -f k8s/monitoring/servicemonitors.yaml
	$(KUBECTL) apply -f k8s/monitoring/alerts.yaml
	$(KUBECTL) apply -f k8s/monitoring/grafana-dashboard.yaml

alerts-apply:          ## Re-apply just the alert rules + service monitors
	$(KUBECTL) apply -f k8s/monitoring/servicemonitors.yaml
	$(KUBECTL) apply -f k8s/monitoring/alerts.yaml

bootstrap: k8s-up monitoring-up   ## Full bring-up: app + monitoring
	@echo "All up. Grafana: kubectl -n $(MON_NS) port-forward svc/$(MON_RELEASE)-grafana 3000:80"
