resource "kubernetes_namespace_v1" "platform" {
  for_each = var.namespaces

  metadata {
    name = each.value

    labels = {
      managed-by = "terraform"
      project    = "data-platform-lab"
    }
  }
}
