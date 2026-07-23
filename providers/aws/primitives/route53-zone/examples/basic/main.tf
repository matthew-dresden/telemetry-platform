module "example" {
  source = "../../"

  zone_name     = var.zone_name
  comment       = var.comment
  force_destroy = var.force_destroy
  tags          = var.tags
}
