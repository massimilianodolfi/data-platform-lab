output "namespaces" {
  description = "Namespaces managed by Terraform"

  value = [
    for namespace in kubernetes_namespace_v1.platform :
    namespace.metadata[0].name
  ]
}
