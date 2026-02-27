# -------------------------------------------------------------------
# Lambda function + IAM
# -------------------------------------------------------------------

# IAM execution role
resource "aws_iam_role" "lambda_role" {
  name = "${var.project_name}-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

# CloudWatch Logs
resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# DynamoDB, S3, Secrets Manager, SSM access
resource "aws_iam_role_policy" "lambda_policy" {
  name = "${var.project_name}-lambda-policy"
  role = aws_iam_role.lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DynamoDB"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem",
          "dynamodb:Query",
        ]
        Resource = aws_dynamodb_table.alert_state.arn
      },
      {
        Sid    = "S3Reports"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject",
        ]
        Resource = "${aws_s3_bucket.reports.arn}/*"
      },
      {
        Sid    = "SecretsManager"
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
        ]
        Resource = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:${var.secret_name}*"
      },
      {
        Sid    = "SSM"
        Effect = "Allow"
        Action = [
          "ssm:GetParameter",
        ]
        Resource = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter${var.ssm_config_path}"
      },
      {
        Sid    = "Bedrock"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
        ]
        Resource = "arn:aws:bedrock:${var.aws_region}::foundation-model/anthropic.claude-3-haiku-20240307-v1:0"
      },
    ]
  })
}

# Lambda function
resource "aws_lambda_function" "analyzer" {
  function_name = var.project_name
  description   = "Datadog Log Analyzer — 24/7 monitoring agent"
  role          = aws_iam_role.lambda_role.arn
  handler       = "dd_log_analyzer.lambda_handler.handler"
  runtime       = "python3.11"
  architectures = ["x86_64"]
  memory_size   = var.lambda_memory
  timeout       = var.lambda_timeout

  filename         = "${path.module}/../dist/lambda.zip"
  source_code_hash = filebase64sha256("${path.module}/../dist/lambda.zip")

  environment {
    variables = {
      SECRET_NAME          = var.secret_name
      SSM_CONFIG_PATH      = var.ssm_config_path
      DYNAMODB_TABLE       = aws_dynamodb_table.alert_state.name
      S3_REPORT_BUCKET     = aws_s3_bucket.reports.id
      AWS_REGION_NAME      = var.aws_region
      ANALYZE_ALL_SERVICES = "true"
      BEDROCK_MODEL_ID     = "anthropic.claude-3-haiku-20240307-v1:0"
    }
  }

  tags = {
    Name = var.project_name
  }
}

# CloudWatch log group with retention
resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${var.project_name}"
  retention_in_days = 14
}
