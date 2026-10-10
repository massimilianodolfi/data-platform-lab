variable "namespaces" {
  description = "Kubernetes namespaces used by the data platform"
  type        = set(string)

  default = [
    "spark",
    "storage"
  ]
}
