output "lambda_function_name" {
  description = "Name of the Lambda function"
  value       = aws_lambda_function.analyzer.function_name
}

output "lambda_function_arn" {
  description = "ARN of the Lambda function"
  value       = aws_lambda_function.analyzer.arn
}

output "dynamodb_table_name" {
  description = "Name of the DynamoDB alert state table"
  value       = aws_dynamodb_table.alert_state.name
}

output "s3_reports_bucket" {
  description = "Name of the S3 reports bucket"
  value       = aws_s3_bucket.reports.id
}

output "eventbridge_rule_arn" {
  description = "ARN of the EventBridge schedule rule"
  value       = aws_cloudwatch_event_rule.schedule.arn
}

output "cloudwatch_log_group" {
  description = "CloudWatch log group for Lambda"
  value       = aws_cloudwatch_log_group.lambda.name
}
