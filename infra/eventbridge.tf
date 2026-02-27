# -------------------------------------------------------------------
# EventBridge — 5-minute schedule
# -------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "schedule" {
  name                = "${var.project_name}-schedule"
  description         = "Trigger dd-log-analyzer Lambda every 5 minutes"
  schedule_expression = var.schedule_rate

  tags = {
    Name = "${var.project_name}-schedule"
  }
}

resource "aws_cloudwatch_event_target" "lambda" {
  rule      = aws_cloudwatch_event_rule.schedule.name
  target_id = "${var.project_name}-lambda"
  arn       = aws_lambda_function.analyzer.arn
}

resource "aws_lambda_permission" "eventbridge" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.analyzer.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.schedule.arn
}
