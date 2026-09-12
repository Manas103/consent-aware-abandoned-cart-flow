output "alb_dns_name" {
  value = aws_lb.api.dns_name
}

output "rds_endpoint" {
  value = aws_db_instance.mysql.address
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.main.name
}
