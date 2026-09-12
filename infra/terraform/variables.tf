variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "ecr_repository_url" {
  type        = string
  description = "ECR repository URL holding the api/worker images. Placeholder, never resolved against a real registry in this build."
  default     = "123456789012.dkr.ecr.us-east-1.amazonaws.com/cartflow"
}

variable "db_name" {
  type    = string
  default = "cartflow"
}

variable "db_username" {
  type    = string
  default = "cartflow"
}

variable "db_password" {
  type      = string
  sensitive = true
  default   = "changeme-in-real-deployment"
}

variable "db_instance_class" {
  type    = string
  default = "db.t3.micro"
}

variable "api_desired_count" {
  type    = number
  default = 2
}

variable "worker_desired_count" {
  type    = number
  default = 2
}
