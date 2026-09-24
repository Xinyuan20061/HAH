const {findExercise}=require('../../utils/exercises')
Page({
  data:{exercise:null},
  onLoad(options){
    const name=decodeURIComponent(options.name||'训练动作')
    this.setData({exercise:findExercise(name)})
  }
})
