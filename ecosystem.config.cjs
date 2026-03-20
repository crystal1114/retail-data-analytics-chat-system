module.exports = {
  apps: [
    {
      name: 'retail-backend',
      script: '/bin/bash',
      args: '-c "cd /home/user/retail-analytics && DATABASE_PATH=data/retail.db uvicorn backend.app.main:app --host 0.0.0.0 --port 8000"',
      env: {
        DATABASE_PATH: 'data/retail.db',
        PYTHONPATH: '/home/user/retail-analytics'
      },
      watch: false,
      instances: 1,
      exec_mode: 'fork'
    }
  ]
}
