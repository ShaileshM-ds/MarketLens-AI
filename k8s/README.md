# Kubernetes deployment

Run these commands from D:\Projects\minor project\MarketLens-AI-source. These files add a Kubernetes deployment alongside Docker Compose. Applying them creates a separate MongoDB volume; existing Compose data is not migrated or modified.

## Included resources

| Resource | Purpose |
| --- | --- |
| Namespace marketlens-ai | Isolates this application |
| Frontend Deployment and Service | React build served by non-root Nginx on 8080; Service port 80 |
| Backend Deployment and Service | FastAPI on 8000, startup/readiness/liveness probes |
| Worker Deployment | Background jobs and alerts; waits for the backend, no inbound Service |
| MongoDB StatefulSet and headless Service | Authenticated MongoDB 8, one replica, 5 GiB persistent volume claim |
| Ingress | Same-host routes /api, /docs and /openapi.json to backend; / to frontend |
| ConfigMaps | Application environment and Kubernetes-specific Nginx config |
| Secret example | MongoDB connection credentials and optional provider keys, never included automatically |
| NetworkPolicies | Only backend/worker can reach MongoDB; worker accepts no inbound connections |
| HorizontalPodAutoscalers | Frontend scales locally; frontend and backend scale in the production overlay |

All workloads have resource requests/limits, non-root UIDs, dropped capabilities, disabled service-account token mounts and writable scratch volumes. Backend liveness checks the process, while readiness checks MongoDB. Worker readiness checks database connectivity; it is not proof that a particular job completed. ConfigMap hashes trigger replacement pods when configuration changes.

## Prerequisites

- A Linux-node Kubernetes cluster and kubectl. Check the intended context before applying anything.
- A default StorageClass with dynamic provisioning, or a suitable PersistentVolume for the MongoDB claim. Adjust size/storageClassName in base/mongo-statefulset.yaml for your cluster.
- For Ingress, an installed ingress controller and a default IngressClass. This package does not install a controller. If there is no default, add spec.ingressClassName to base/ingress.yaml using your installed class. Port-forwarding works without an ingress controller.
- A NetworkPolicy-capable CNI for the supplied policies to take effect.
- A working resource metrics API (usually Metrics Server) for CPU autoscaling. The package does not install Metrics Server or weaken its TLS validation.
- Both application images available on every node. Local Docker images are not automatically visible to every Kubernetes runtime.

~~~powershell
kubectl config current-context
kubectl get nodes
kubectl get storageclass
kubectl get ingressclass
~~~

## Local deployment

1. Build the images from the repository root. The backend image is reused by the worker and init containers.

~~~powershell
docker build -f backend/Dockerfile -t marketlens-ai-backend:k8s-local .
docker build -f frontend/Dockerfile -t marketlens-ai-frontend:k8s-local .
~~~

Load these images into your node runtime if necessary. For Minikube use minikube image load IMAGE; for Kind use kind load docker-image IMAGE --name YOUR_CLUSTER. For Docker Desktop, check whether its selected Kubernetes provisioner shares the Docker image store. If using a registry, change both image mappings in the overlay and configure imagePullSecrets when the registry is private.

2. Create the namespace and credentials. Copy the example, replace the password and its matching URL-encoded password in mongo-uri, then apply only your local file. The empty provider keys can stay empty while live collection is disabled.

~~~powershell
kubectl apply -f k8s/base/namespace.yaml
Copy-Item k8s/secret.example.yaml k8s/secret.local.yaml
# Edit k8s/secret.local.yaml with real credentials before the next command.
kubectl apply -f k8s/secret.local.yaml
~~~

The local secret file is ignored by Git. Use a secret manager for deployed credentials. MongoDB creates its initial account only on an empty volume: changing this Secret later does not rotate an existing database password. Keep the configured URI and the actual database credentials in agreement. The included local bootstrap URI uses the MongoDB administrator account; use a dedicated application account with only the required database permissions for an external deployment.

3. Preview, validate against your API server, and deploy. kubectl apply -k k8s selects the local overlay. Do not apply the example Secret or an entire directory recursively.

~~~powershell
kubectl kustomize k8s
kubectl apply --dry-run=server -k k8s
kubectl apply -k k8s
kubectl -n marketlens-ai rollout status statefulset/mongo --timeout=300s
kubectl -n marketlens-ai rollout status deployment/backend --timeout=900s
kubectl -n marketlens-ai rollout status deployment/worker --timeout=300s
kubectl -n marketlens-ai rollout status deployment/frontend --timeout=300s
kubectl -n marketlens-ai get pods,services,ingress,pvc
~~~

The backend waits for MongoDB, and the worker waits for the backend. The local configuration seeds an empty demo database using the existing application code. Backend startup allows ten minutes for that initial work. The local backend and the worker stay at one replica with Recreate updates to avoid overlapping bootstrap/polling processes; there is a short interruption on updates. Frontend replica count is controlled by its HPA. The production backend uses rolling updates and HPA with automatic seeding disabled. MongoDB is a single instance, not a highly available replica set.

4. Access the UI without Ingress:

~~~powershell
kubectl -n marketlens-ai port-forward service/frontend 8088:80
~~~

Open http://localhost:8088. The Kubernetes Nginx config proxies API traffic to the backend Service, so this path exercises the whole app. If Docker Compose already occupies 8088, stop its frontend or choose a different forwarding port and add that exact origin to the ConfigMap ALLOWED_ORIGINS before redeploying.

For Ingress, point marketlens.local at your controller's reachable address using local DNS or your Windows hosts file. Open http://marketlens.local. The ingress controller address is not necessarily localhost. The Ingress defines routes only; it does not create DNS or install a load balancer/controller. All application Services are internal ClusterIP Services.

## HTTPS deployment template

overlays/production is an alternative configuration for a real hostname, secure cookies and a pre-created TLS Secret. It is a starting configuration, not a claim of production validation. Both overlays target the same namespace, so choose one per target cluster.

Before using it:

1. Push both images to your registry; replace registry.example.com and replace-with-version in its kustomization.yaml with your immutable release tags or digests.
2. Replace marketlens.example.com in its ingress.yaml and ConfigMap ALLOWED_ORIGINS. Set your ingressClassName if needed.
3. Provision the namespace and real secrets, then create a kubernetes.io/tls Secret named marketlens-tls in that namespace using your certificate and key, or arrange certificate management to create it.
4. Set storage class/capacity, backups, database least-privilege credentials and access policies for your cluster. The bundled single MongoDB instance needs a deliberate recovery/availability plan. The policies limit MongoDB ingress; they do not restrict outbound provider traffic.
5. Configure registry imagePullSecrets if needed. Live provider access stays disabled unless you explicitly set ENABLE_LIVE=1 and supply provider credentials and bounded limits.

~~~powershell
kubectl kustomize k8s/overlays/production
kubectl apply --dry-run=server -k k8s/overlays/production
kubectl apply -k k8s/overlays/production
~~~

This overlay sets PRODUCTION=1, AUTO_SEED=0 and HTTPS-only allowed origins. It uses the application's secure cookie behavior. Use HTTPS for the UI; plain HTTP port-forwarding cannot carry those secure session cookies. Existing synthetic observations are not deleted when auto-seeding is disabled. If you need demo data in this environment, seed deliberately using the existing management procedure before serving users.

## Horizontal pod autoscaling

The manifests use autoscaling/v2 with average CPU utilization at 70% of the requested CPU. This is a percentage of resource requests, not the CPU limits. Both target Deployments already declare CPU requests. These initial thresholds should be tuned with application load measurements and available node capacity.

| Overlay | Workload | Minimum pods | Maximum pods | CPU target |
| --- | --- | --- | --- | --- |
| Local/default | Frontend | 1 | 3 | 70% |
| Production | Frontend | 2 | 6 | 70% |
| Production | Backend | 2 | 6 | 70% |

Scale-up allows at most two additional pods per 60 seconds. Scale-down waits for a five-minute stabilization window, then removes at most one pod per 60 seconds. HPA controls the target replica count, so the frontend Deployment and the production backend patch intentionally omit spec.replicas. Do not add a Kustomize replica override to these targets; repeated apply operations would otherwise compete with the autoscaler.

The local backend has no HPA because AUTO_SEED=1 can cause concurrent bootstrap work on an empty demo database. The production overlay sets AUTO_SEED=0 before enabling backend HPA. Keep automatic seeding disabled whenever the backend is horizontally scaled. MongoDB and the polling worker stay at one replica; increasing database replicas needs a replica-set design, and worker scaling needs separate concurrency/load validation.

Check metrics support before relying on automatic scaling:

~~~powershell
kubectl get apiservice v1beta1.metrics.k8s.io
kubectl top nodes
kubectl -n marketlens-ai top pods
kubectl -n marketlens-ai get hpa
kubectl -n marketlens-ai describe hpa frontend
~~~

If the metrics API is missing or HPA shows unknown CPU targets, install/configure a compatible Metrics Server through your cluster's supported add-on procedure and verify its APIService is Available. HPA objects can be accepted by the API even when metrics are unavailable; that does not demonstrate working scaling. Do not solve metrics TLS errors by disabling certificate verification.

The HPA resources are included automatically in the existing overlay apply commands. To observe scaling after deploying and generating representative traffic:

~~~powershell
kubectl -n marketlens-ai get hpa -w
kubectl -n marketlens-ai get deployments
# Production only:
kubectl -n marketlens-ai describe hpa backend
~~~

When migrating an already-deployed target from fixed replicas to HPA ownership, coordinate removal of the previously applied spec.replicas field. The first apply that removes it may reset the default count before HPA reconciles; plan this transition and confirm the minimum replicas afterward. Removing an HPA from a manifest does not delete an existing HPA automatically; explicitly remove an old backend HPA before switching a scaled production installation back to the single-backend demo overlay.

## Operations and troubleshooting

~~~powershell
kubectl -n marketlens-ai logs deployment/backend --tail=100
kubectl -n marketlens-ai logs deployment/worker --tail=100
kubectl -n marketlens-ai logs statefulset/mongo --tail=100
kubectl -n marketlens-ai get events --sort-by=.metadata.creationTimestamp
kubectl -n marketlens-ai describe pod POD_NAME
~~~

Create an application administrator interactively with:

~~~powershell
kubectl -n marketlens-ai exec -it deployment/backend -- env PYTHONPATH=/app python scripts/manage.py admin --mode demo
~~~

For imported/live mode, replace the final mode argument. No administrator password is embedded in the manifests.

- ImagePullBackOff: load the image into the cluster or correct registry credentials and image mappings.
- Pending MongoDB: inspect the PVC, default StorageClass and node capacity.
- CreateContainerConfigError: create marketlens-secrets with the keys shown in the example.
- Backend init failures: inspect the init-container logs and confirm MongoDB readiness and its authenticated URI.
- Frontend 502: check backend readiness and Service endpoints.
- Ingress 404: check hostname resolution, controller address, configured class and path rules.

To remove only the app processes and routing, delete deployments frontend/backend/worker, StatefulSet mongo, Services frontend/backend/mongo and Ingress marketlens in this namespace. This retains mongo-data-mongo-0. Deleting the namespace also deletes the PVC; do not do that when you need its database. PVC/PV retention depends on your storage provider and reclaim policy, and is not a backup.

## References

- [Kustomize with kubectl](https://kubernetes.io/docs/tasks/manage-kubernetes-objects/kustomization/)
- [Ingress prerequisites and routing](https://kubernetes.io/docs/concepts/services-networking/ingress/)
- [StatefulSets and persistent storage](https://kubernetes.io/docs/concepts/workloads/controllers/statefulset/)

- [Kubernetes HPA behavior, resource requests and metrics](https://kubernetes.io/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale/)
