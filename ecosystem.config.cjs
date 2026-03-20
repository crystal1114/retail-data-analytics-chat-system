module.exports = {
  apps: [
    {
      name: 'retail-backend',
      script: '/home/user/retail-analytics/start_backend.sh',
      env: {
        PYTHONPATH: '/home/user/retail-analytics'
      },
      watch: false,
      instances: 1,
      exec_mode: 'fork'
    },
    {
      name: 'retail-frontend',
      script: 'npx',
      args: 'vite preview --host 0.0.0.0 --port 5173',
      cwd: '/home/user/retail-analytics/frontend',
      watch: false,
      instances: 1,
      exec_mode: 'fork'
    }
  ]
}
