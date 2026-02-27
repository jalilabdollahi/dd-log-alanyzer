# -------------------------------------------------------------------
# DynamoDB — alert dedup state
# -------------------------------------------------------------------

resource "aws_dynamodb_table" "alert_state" {
  name         = "${var.project_name}-alert-state"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "fingerprint"

  attribute {
    name = "fingerprint"
    type = "S"
  }

  ttl {
    attribute_name = "ttl_expiry"
    enabled        = true
  }

  tags = {
    Name = "${var.project_name}-alert-state"
  }
}
